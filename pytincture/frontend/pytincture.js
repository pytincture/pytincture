
async function runTinctureApp(application, widgetlib){
    const PYODIDE_BASE_URL = "https://cdn.jsdelivr.net/pyodide/v0.24.0/full/"
    let pyodide = await loadPyodide({ indexURL: PYODIDE_BASE_URL })
    let ptResponse = await fetch("appcode/pytincture.zip");
    let ptBinary = await ptResponse.arrayBuffer();
    pyodide.unpackArchive(ptBinary, "zip");
    let zipResponse = await fetch("appcode/ui_data.zip");
    let zipBinary = await zipResponse.arrayBuffer();
    pyodide.unpackArchive(zipBinary, "zip");
    let pyApp = (await fetch("appcode/"+application+".py")).text();
    pyodide.runPython((await pyApp).toString());
}
