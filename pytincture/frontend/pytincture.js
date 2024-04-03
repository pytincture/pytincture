
const PYODIDE_BASE_URL = "https://cdn.jsdelivr.net/pyodide/v0.25.0/full/";

async function runTinctureApp(application, widgetlib) {
    let pyodide = await loadPyodide({ indexURL: PYODIDE_BASE_URL });

    // Install and load widget package
    await pyodide.loadPackage("micropip");
    await installAndLoadWidgetset(pyodide, 'dhxpyt');

    let appResponse = await fetch("appcode/appcode.pyt");
    let appBinary = await appResponse.arrayBuffer();
    pyodide.unpackArchive(appBinary, "zip");
    pyodide.runPython("from " + application + " import " + application + " as app\napp()");
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
                # Find all font file names in the folder
                font_files = [f for f in os.listdir(font_folder_path) if os.path.isfile(os.path.join(font_folder_path, f))]

                # Replace URLs in CSS content
                for font_file in font_files:
                    font_file_path = os.path.join(font_folder_path, font_file)
                    with open(font_file_path, "rb") as f:
                        # Read font file content and encode it in base64
                        font_data = base64.b64encode(f.read()).decode("utf-8")

                    # Replace font file URLs in CSS content
                    css_content = re.sub(r"""url\(['\"]?(\.\./)*([^'\"].*?)['\"]?\)""", 
                                        f"url(data:font/{font_file.split('.')[-1]};charset=utf-8;base64,{font_data})", 
                                        css_content)

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
