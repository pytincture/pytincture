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

#Static files endpoint
app.mount("/frontend", StaticFiles(directory="frontend"), name="static")

#Application endpoint
@app.get("/{application}", response_class=HTMLResponse)
async def main(response: Response, application):
    index = open("frontend/index.html").read().replace("***APPLICATION***", application)
    return index

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="debug")
