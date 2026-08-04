
import json
import os
import tempfile

def load_json_registry(json_file):
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"JSON file {json_file} not found. Starting with an empty registry.")
    except json.JSONDecodeError as e:
        msg = f"Error decoding JSON from file {json_file}. Please ensure that the file contains valid JSON."
        raise json.JSONDecodeError(msg, e.doc, e.pos) from e

def save_json_registry(data,json_file):

    json_file.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=json_file.parent, delete=False, suffix='PENDING_.json', encoding='utf-8') as tmpfile:
        with open(tmpfile.name, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    os.replace(tmpfile.name, json_file)

