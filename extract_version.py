# extract_version.py
import re

def extract_version():
    with open('setup.py', 'r') as file:
        content = file.read()
        match = re.search(r"version=['\"]([^'\"]+)['\"]", content)
        if match:
            return match.group(1).replace('.', '')
        else:
            raise ValueError("Version string not found in setup.py")

if __name__ == "__main__":
    print(extract_version())
