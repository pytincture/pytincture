
const PYODIDE_BASE_URL = "https://cdn.jsdelivr.net/pyodide/v0.25.0/full/";

async function runTinctureApp(application, widgetlib) {
    let pyodide = await loadPyodide({ indexURL: PYODIDE_BASE_URL });

    // Install and load dhxpyt package
    await pyodide.loadPackage("micropip");
    await installAndLoadDhxpyt(pyodide);

    // let ptResponse = await fetch("appcode/pytincture.zip");
    // let ptBinary = await ptResponse.arrayBuffer();
    // pyodide.unpackArchive(ptBinary, "zip");
    let appResponse = await fetch("appcode/appcode.pyt");
    let appBinary = await appResponse.arrayBuffer();
    pyodide.unpackArchive(appBinary, "zip");
    pyodide.runPython("from " + application + " import " + application + " as app\napp()");
}

async function installAndLoadDhxpyt(pyodide) {
    try {
        await pyodide.runPythonAsync(`
            import micropip
            await micropip.install('dhxpyt');
        `);

        const packagePath = await pyodide.runPythonAsync(`
            import sysconfig
            import dhxpyt
            sysconfig.get_paths()['purelib']
        `);

        const directoryPath = '/'; // Adjust this path according to the structure of your package

        const registerFilesCode = `
            import os
            from js import pyodide

            def register_files(package_path, directory):
                files = os.listdir(os.path.join(package_path, directory))
                for file in files:
                    file_path = os.path.join(package_path, directory, file)
                    with open(file_path, 'r') as f:
                        content = f.read()
                    virtual_file_path = os.path.join(package_path, directory, file)
                    pyodide.register_file(virtual_file_path, content)

            register_files("${packagePath}", "${directoryPath}")
        `;
        await pyodide.runPythonAsync(registerFilesCode);

        const loadFilesCode = `
            import os
            for root, dirs, files in os.walk(os.path.join("${packagePath}", "${directoryPath}")):
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
                        style = document.createElement('style')
                        style.innerHTML = style_content
                        document.head.appendChild(style)
        `;
        await pyodide.runPythonAsync(loadFilesCode);
    } catch (error) {
        console.error('Error installing and loading dhxpyt:', error);
    }
}
