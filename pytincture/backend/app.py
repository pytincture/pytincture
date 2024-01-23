import os

import uvicorn
from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

app = FastAPI()

origins = (
    "https://pypi.org",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_PATH = os.path.join(os.path.dirname(__file__), "../frontend/")
MODULE_PATH = os.environ.get("MODULES_PATH")

#Static files endpoint
app.mount("/frontend", StaticFiles(directory=STATIC_PATH), name="static")

#Static files endpoint
app.mount("/appcode", StaticFiles(directory=MODULE_PATH), name="static")

@app.get("/appdata", response_class=HTMLResponse)
async def main(function_name, data_module):
    pass

#Application endpoint
@app.get("/{application}", response_class=HTMLResponse)
async def main(response: Response, application):
    index = open(f"{STATIC_PATH}/index.html").read().replace("***APPLICATION***", application)
    return index

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="debug")
