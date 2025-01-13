

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


const PYODIDE_BASE_URL = "https://cdn.jsdelivr.net/pyodide/v0.27.0/full/";

async function runTinctureApp(application, widgetlib) {
    let pyodide = await loadPyodide({ indexURL: PYODIDE_BASE_URL });

    // Install and load the widget package
    await pyodide.loadPackage("micropip");

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

    let widgetUrl = "http://0.0.0.0:8070/appcode/dhxpyt-99.99.99-py3-none-any.whl";
    if (await urlExists(widgetUrl)) {
        // If the URL exists, install and load the widget package from the URL
        await installAndLoadWidgetset(pyodide, widgetUrl);
    } else {
        // Otherwise, fallback to loading the provided widgetlib
        await installAndLoadWidgetset(pyodide, widgetlib);
    }

    // Load and execute the application code
    let appResponse = await fetch("appcode/appcode.pyt");
    let appBinary = await appResponse.arrayBuffer();
    pyodide.unpackArchive(appBinary, "zip");
    pyodide.runPython(
        `from ${application} import ${application} as app\napp()`
    );
}

async function installAndLoadWidgetset(pyodide, widgetlib) {
    try {
        await pyodide.runPythonAsync(`
            import micropip
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
                with open(font_path, 'rb') as f:
                    font_data = f.read()
                return pyodide._module.btoa(pyodide._module.Uint8Array.new(font_data))
            
            # Function to replace font URLs with base64 data URLs in CSS content
            def replace_font_urls(css_content, font_folder_path):
                # Find all font files in the folder (e.g., .woff, .woff2)
                font_files = [f for f in os.listdir(font_folder_path) if os.path.isfile(os.path.join(font_folder_path, f))]

                # Replace URLs in CSS content
                for font_file in font_files:
                    print("replacing font file:", font_file)
                    font_extension = font_file.split('.')[-1].lower()
                    if font_extension in ['woff', 'woff2']:  # Ensure we're dealing with font files
                        font_file_path = os.path.join(font_folder_path, font_file)
                        with open(font_file_path, "rb") as f:
                            # Read font file content and encode it in base64
                            font_data = base64.b64encode(f.read()).decode("utf-8")

                        # Construct the MIME type based on the font extension
                        mime_type = f"font/{font_extension}"

                        # Replace font file URLs in CSS content with base64 encoded font data
                        # Match URL formats like url('./fonts/font.woff') or url('./fonts/font.woff2')
                        css_content = re.sub(
                            rf"""url\(['"]?\.\/fonts\/{re.escape(font_file)}['"]?\)""", 
                            f"url(data:{mime_type};charset=utf-8;base64,{font_data})", 
                            css_content
                        )
                
                return css_content
            
            # Package path
            package_path = site.getsitepackages()[0]
            
            # Iterate through files in the specified directory
            for root, dirs, files in os.walk(package_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    file_extension = os.path.splitext(file_path)[1].lower()
                    if file_extension == '.js':
                        with open(file_path) as f:
                            script_content = f.read()
                        js.eval(script_content)
                    elif file_extension == '.css':
                        with open(file_path) as f:
                            style_content = f.read()
                        # Replace font URLs with base64 data URLs directly in CSS content
                        style_content = replace_font_urls(style_content, os.path.join(os.path.dirname(file_path), "fonts"))
                        # Inject CSS into the document
                        style = js.document.createElement('style')
                        style.innerHTML = style_content
                        js.document.head.appendChild(style)
        
            `;
        await pyodide.runPythonAsync(loadFilesCode);
    } catch (error) {
        console.error('Error installing and loading ${widgetlib}:', error);
    }
}
