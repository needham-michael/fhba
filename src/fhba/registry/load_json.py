
import json

def load_json_registry(json_file):
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"JSON file {json_file} not found. Starting with an empty registry.")
    except json.JSONDecodeError as e:
        msg = f"Error decoding JSON from file {json_file}. Please ensure that the file contains valid JSON."
        raise json.JSONDecodeError(msg, e.doc, e.pos) from e