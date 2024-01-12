
function runTinctureApp(application){
    languagePluginLoader.then(async () => {
        await pyodide.loadPackage(['micropip']);
        const response = await fetch('frontend/'+application+'.py');
        const pyCode = await response.text();
        await pyodide.runPythonAsync(pyCode);
    });
}
