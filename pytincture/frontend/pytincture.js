
const PYODIDE_BASE_URL = "https://cdn.jsdelivr.net/pyodide/v0.25.0/full/"

async function runTinctureApp(application, widgetlib){
    let pyodide = await loadPyodide({ indexURL: PYODIDE_BASE_URL })
    let ptResponse = await fetch("appcode/pytincture.zip");
    let ptBinary = await ptResponse.arrayBuffer();
    pyodide.unpackArchive(ptBinary, "zip");
    let appResponse = await fetch("appcode/appcode.pyt");
    let appBinary = await appResponse.arrayBuffer();
    pyodide.unpackArchive(appBinary, "zip");
    let pyApp = (await fetch("appcode/"+application+".py")).text();
    pyodide.runPython((await pyApp).toString()+"\n"+application+"()");
}
