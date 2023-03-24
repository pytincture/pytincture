async function loadPyodideAndRunScript(script) {
    //  Load Pyodide
    const pyodideUrl = 'https://cdn.jsdelivr.net/pyodide/v0.18.1/full/pyodide.js';
    const pyodide = await loadPyodide({ indexURL: pyodideUrl });
  
    // Fetch the script file
    const response = await fetch(script);
    const code = await response.text();

    pyodide._module.FS.writeFile('/myfile.py', code);
    pyodide.runPython('import myfile');
  
    // Execute the code using Pyodide
    await pyodide.loadPackage(['sys']);
    pyodide.runPython(code);
  }
  

  