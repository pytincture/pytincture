

// Function to send logs to the backend
function sendToBackend(level, message) {
    fetch("/logs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ level: level, message: message, timestamp: new Date().toISOString() }),
    }).catch(err => {
        // Only log the failure to send, don't interfere with the original log
        console.error("Failed to send log to backend:", err);
    });
}

// Override console methods
["log", "warn", "error", "info", "debug"].forEach(level => {
    const originalMethod = console[level]; // Keep the original console method
    console[level] = function (...args) {
        const message = args.map(arg => (typeof arg === "object" ? JSON.stringify(arg) : arg)).join(" ");

        // Send the log to the backend
        sendToBackend(level, message);

        // Call the original console method to display logs in the browser console
        originalMethod.apply(console, args);
    };
});

const PYODIDE_BASE_URL = "https://cdn.jsdelivr.net/pyodide/v0.28.0/full/";

// Custom HTTP request functions that Python can use
function createPythonHttpMethods() {
    return {
        request_json: async function(url, payload = null, method = 'GET', headers = {}) {
            try {
                const config = {
                    method: method,
                    headers: {
                        'Content-Type': 'application/json',
                        ...headers
                    }
                };
                
                if (payload && (method === 'POST' || method === 'PUT' || method === 'PATCH')) {
                    config.body = typeof payload === 'string' ? payload : JSON.stringify(payload);
                }
                
                const response = await fetch(url, config);
                
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                
                return await response.json();
            } catch (error) {
                console.error('HTTP request failed:', error);
                throw error;
            }
        },
        
        http_json: function(url, payload = null, method = 'GET', headers = {}) {
            // Synchronous version using XMLHttpRequest for compatibility
            return new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                xhr.open(method, url, false); // false = synchronous
                
                // Set headers
                xhr.setRequestHeader('Content-Type', 'application/json');
                Object.keys(headers).forEach(key => {
                    xhr.setRequestHeader(key, headers[key]);
                });
                
                xhr.onload = function() {
                    if (xhr.status >= 200 && xhr.status < 300) {
                        try {
                            resolve(JSON.parse(xhr.responseText));
                        } catch (e) {
                            resolve(xhr.responseText);
                        }
                    } else {
                        reject(new Error(`HTTP ${xhr.status}: ${xhr.statusText}`));
                    }
                };
                
                xhr.onerror = function() {
                    reject(new Error('Network error'));
                };
                
                try {
                    if (payload && (method === 'POST' || method === 'PUT' || method === 'PATCH')) {
                        xhr.send(typeof payload === 'string' ? payload : JSON.stringify(payload));
                    } else {
                        xhr.send();
                    }
                } catch (error) {
                    reject(error);
                }
            });
        },

        pyfetch_wrapper: async function(url, options = {}) {
            // Wrapper around fetch for Python compatibility
            try {
                const response = await fetch(url, options);
                return {
                    ok: response.ok,
                    status: response.status,
                    statusText: response.statusText,
                    json: async () => await response.json(),
                    text: async () => await response.text(),
                    arrayBuffer: async () => await response.arrayBuffer()
                };
            } catch (error) {
                console.error('Pyfetch wrapper failed:', error);
                throw error;
            }
        }
    };
}

async function runTinctureApp(application, widgetlib, entrypoint) {
    let pyodide = await loadPyodide({ indexURL: PYODIDE_BASE_URL });

    // Install and load the widget package
    await pyodide.loadPackage("micropip");

    // Expose HTTP methods to Python
    const httpMethods = createPythonHttpMethods();
    pyodide.globals.set("js_request_json", httpMethods.request_json);
    pyodide.globals.set("js_http_json", httpMethods.http_json);
    pyodide.globals.set("js_pyfetch_wrapper", httpMethods.pyfetch_wrapper);

    // Function to check if the URL exists
    async function urlExists(url) {
        try {
            let response = await fetch(url, { method: "HEAD" });
            return response.ok;
        } catch (err) {
            console.warn(`Failed to check URL: ${url}`, err);
            return false;
        }
    }

    let widgetUrl = `http://0.0.0.0:8070/${application}/appcode/dhxpyt-99.99.99-py3-none-any.whl`;
    if (await urlExists(widgetUrl)) {
        // If the URL exists, install and load the widget package from the URL
        await installAndLoadWidgetset(pyodide, widgetUrl);
    } else {
        // Otherwise, fallback to loading the provided widgetlib
        await installAndLoadWidgetset(pyodide, widgetlib);
    }

    // Load and execute the application code
    try {
        let appResponse = await fetch(application + "/appcode/appcode.pyt");
        if (!appResponse.ok) {
            throw new Error(`Failed to load application code: ${appResponse.status} ${appResponse.statusText}`);
        }
        let appBinary = await appResponse.arrayBuffer();
        pyodide.unpackArchive(appBinary, "zip");
        
        // Set up HTTP request alternatives in Python before running the app
        await pyodide.runPythonAsync(`
            # Create wrapper functions that Python can use instead of disabled fetch
            import js
            import json as json_module
            
            async def request_json(url, payload=None, method='GET', headers=None):
                """Async HTTP request function for Python"""
                if headers is None:
                    headers = {}
                result = await js.js_request_json(url, payload, method, headers)
                return result
            
            def http_json(url, payload=None, method='GET', headers=None):
                """Sync HTTP request function for Python"""
                if headers is None:
                    headers = {}
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(js.js_http_json(url, payload, method, headers))
                    return result
                finally:
                    loop.close()
            
            # Make these available globally
            globals()['request_json'] = request_json
            globals()['http_json'] = http_json
        `);
        
        // Use the entrypoint class name instead of assuming it's the same as the application name
        pyodide.runPython(
            `from ${application} import ${entrypoint} as app\napp()`
        );
    } catch (error) {
        console.error("Failed to run Tincture app:", error);
        throw error;
    }
}

async function installAndLoadWidgetset(pyodide, widgetlib) {
    try {
        await pyodide.runPythonAsync(`
            import micropip
            await micropip.install("python-dotenv")
            await micropip.install('${widgetlib}');
        `);

        const loadFilesCode = `
            import os
            import pyodide
            import js
            import site
            import base64
            import re
            
            # Function to convert font file to base64 string
            def font_to_base64(font_path):
                try:
                    with open(font_path, 'rb') as f:
                        font_data = f.read()
                    return pyodide._module.btoa(pyodide._module.Uint8Array.new(font_data))
                except Exception as e:
                    print(f"Error converting font to base64: {e}")
                    return None
            
            # Function to replace font URLs with base64 data URLs in CSS content
            def replace_font_urls(css_content, font_folder_path):
                try:
                    # Check if font folder exists
                    if not os.path.exists(font_folder_path):
                        print(f"Font folder not found: {font_folder_path}")
                        return css_content
                        
                    # Find all font files in the folder (e.g., .woff, .woff2)
                    font_files = [f for f in os.listdir(font_folder_path) 
                                 if os.path.isfile(os.path.join(font_folder_path, f))]

                    # Replace URLs in CSS content
                    for font_file in font_files:
                        print("replacing font file:", font_file)
                        font_extension = font_file.split('.')[-1].lower()
                        if font_extension in ['woff', 'woff2', 'ttf', 'otf']:  # Support more font types
                            font_file_path = os.path.join(font_folder_path, font_file)
                            try:
                                with open(font_file_path, "rb") as f:
                                    # Read font file content and encode it in base64
                                    font_data = base64.b64encode(f.read()).decode("utf-8")

                                # Construct the MIME type based on the font extension
                                mime_type = f"font/{font_extension}"

                                # Replace font file URLs in CSS content with base64 encoded font data
                                # Match URL formats like url('./fonts/font.woff') or url('./fonts/font.woff2')
                                css_content = re.sub(
                                    rf"""url\\(['"]?\\.\/fonts\/{re.escape(font_file)}['"]?\\)""", 
                                    f"url(data:{mime_type};charset=utf-8;base64,{font_data})", 
                                    css_content
                                )
                            except Exception as e:
                                print(f"Error processing font file {font_file}: {e}")
                                
                except Exception as e:
                    print(f"Error in replace_font_urls: {e}")
                
                return css_content
            
            # Package path
            try:
                package_path = site.getsitepackages()[0]
                
                # Iterate through files in the specified directory
                for root, dirs, files in os.walk(package_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        file_extension = os.path.splitext(file_path)[1].lower()
                        
                        try:
                            if file_extension == '.js':
                                with open(file_path, 'r', encoding='utf-8') as f:
                                    script_content = f.read()
                                js.eval(script_content)
                            elif file_extension == '.css':
                                with open(file_path, 'r', encoding='utf-8') as f:
                                    style_content = f.read()
                                # Replace font URLs with base64 data URLs directly in CSS content
                                fonts_path = os.path.join(os.path.dirname(file_path), "fonts")
                                style_content = replace_font_urls(style_content, fonts_path)
                                # Inject CSS into the document
                                style = js.document.createElement('style')
                                style.innerHTML = style_content
                                js.document.head.appendChild(style)
                        except Exception as e:
                            print(f"Error processing file {file_path}: {e}")
                            
            except Exception as e:
                print(f"Error in loadFilesCode: {e}")
        `;
        await pyodide.runPythonAsync(loadFilesCode);
    } catch (error) {
        console.error(`Error installing and loading ${widgetlib}:`, error);
        throw error;
    }
}
