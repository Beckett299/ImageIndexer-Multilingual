import os
import pathlib
import time
from tinydb import TinyDB, Query
import exiftool
import xxhash
import mimetypes
import random
import json
import requests
import base64
from json_repair import repair_json
import re
import argparse
import io
from PIL import Image

class LLMProcessor:
    def __init__(self, config):
        self.config = config
        
        self.api_function_urls = {
            "tokencount": "/api/extra/tokencount",
            "interrogate": "/api/v1/generate",
            "max_context_length": "/api/extra/true_max_context_length",
            "check": "/api/generate/check",
            "abort": "/api/extra/abort",
            "version": "/api/extra/version",
            "model": "/api/v1/model",
            "generate": "/api/v1/generate",
        }
        
        self.image_instruction = config.image_instruction
        
        self.metadata_instruction = "The following caption and metadata was given for an image. Use that to determine the title, IPTC keywords, summary, and subject. Return as JSON object with keys Title, Keywords, Summary, and Subject.\n"
        
        self.api_url = config.api_url
        
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_password}",
        }
        
        self.genkey = self._create_genkey()
        
        self.templates = {
            1: {
                "name": "Alpaca",
                "user": "\n\n### Instruction:\n\n",
                "assistant": "\n\n### Response:\n\n",
                "system": "" #"Below is an instruction that describes a task. Write a response that  appropriately completes the request.",
                },
            2: {
                "name": ["Vicuna", "Wizard", "ShareGPT", "Qwen"], 
                "user": "### Human: ", 
                "assistant": "\n### Assistant: ", 
                "system": ""
                },
            3: {
                "name": ["Llama 2", "Llama2", "Llamav2"], 
                "user": "[INST] ", 
                "assistant": " [/INST]",
                "system": ""
                },
            4: {
                "name": ["Llama 3", "Llama3", "Llama-3"],
                "user": "<|eot_id|><|start_header_id|>user<|end_header_id|>\n\n",
                "assistant": "<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n",
                "system": ""
                },
            5: {
                "name": "Phi-3",
                "user": "<|end|><|user|>\n",
                "assistant": "<end_of_turn><|end|><|assistant|>\n",
                "system": ""
                },
            6: {
                "name": ["Mistral", "bakllava"], 
                "user": "\n[INST] ", 
                "assistant": " [/INST]\n",
                "system": ""
                },
            7: {
                "name": ["Yi"],
                "user": "<|user|>", #<|endoftext|>
                "assistant": "<|assistant|>",
                "system": ""
                },
            8: {
                "name": ["ChatML", "obsidian", "Nous", "Hermes", "llava-v1.6-34b"],
                "user": "<|im_start|>user\n",
                "assistant": "<|im_end|>\n<|im_start|>assistant\n",
                "system": ""
                },
            9: {
                "name": ["WizardLM"],
                "user": "input:\n",
                "assistant": "output\n",
                "system": ""
                }    
            }
        
        self.model = self._get_model()
        self.max_context = self._get_max_context_length()


    def _call_api(self, api_function, payload=None):
        if api_function not in self.api_function_urls:
            raise ValueError(f"Invalid API function: {api_function}")
        url = f"{self.api_url}{self.api_function_urls[api_function]}"

        try:
            if api_function in ["tokencount", "generate", "check", "interrogate"]:
                response = requests.post(url, json=payload, headers=self.headers)
                result = response.json()
                if api_function == "tokencount":
                    return int(result.get("value"))
                else:
                    return result["results"][0].get("text")
            else:
                response = requests.get(url, json=payload, headers=self.headers)
                result = response.json()
                return result.get("result", None)
        
        except requests.RequestException as e:
            print(f"Error calling API: {str(e)}")
            return None

    def interrogate_image(self, image_path):
        if isinstance(image_path, io.BytesIO):
            base64_image = base64.b64encode(image_path.getvalue()).decode("utf-8")
        else:
            with open(image_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode("utf-8")
        
        prompt = self.get_prompt(self.image_instruction, content="")
        payload = {
            "prompt": prompt,
            "images": [base64_image],
            "max_length": 150,
            "genkey": self.genkey,
            "model": "clip",
            "temperature": 0.1,
        }

        return self._call_api("interrogate", payload)

    def describe_content(self, instruction="", content=""):
        prompt = self.get_prompt(instruction, content)
        payload = {
            "prompt": prompt,
            "max_length": 256,
            "genkey": self.genkey,
            "top_p": 1,
            "top_k": 0,
            "temp": 0.5,
            "rep_pen": 1,
            "min_p": 0.05,
        }

        return self._call_api("generate", payload)

    def _get_model(self):
        model_name = self._call_api("model")
        if not model_name:
            return None

        def normalize(s):
            return re.sub(r"[^a-z0-9]", "", s.lower())

        normalized_model_name = normalize(model_name.lower())

        def check_match(template_name):
            if isinstance(template_name, list):
                return any(normalize(name) in normalized_model_name for name in template_name)
            return normalize(template_name) in normalized_model_name

        matched_template = max(
            (
                (template, len(normalize(template["name"] if isinstance(template["name"], str) else template["name"][0])))
                for template in self.templates.values()
                if check_match(template["name"])
            ),
            key=lambda x: x[1],
            default=(None, 0)
        )[0]

        return matched_template if matched_template else self.templates[1]

    def get_prompt(self, instruction="", content=""):
        user_part = self.model["user"]
        assistant_part = self.model["assistant"]
        
        prompt = f"{user_part}{instruction}{content}{assistant_part}"
        print(f"Querying LLM with prompt:\n{prompt}")
        return prompt

    @staticmethod
    def _create_genkey():
        return f"KCP{''.join(str(random.randint(0, 9)) for _ in range(4))}"

    def _get_max_context_length(self):
        return self._call_api("max_context_length")

    def _get_token_count(self, content):
        payload = {"prompt": content, "genkey": self.genkey}
        return self._call_api("tokencount", payload)


class Config:
    def __init__(self):
        self.directory = None
        self.api_url = None
        self.api_password = None
        self.no_crawl = False
        self.force_rehash = False
        self.overwrite = False
        self.dry_run = False
        self.write_keywords = False
        self.write_title = False
        self.write_subject = False
        self.write_description = False
        self.write_caption = False
        self.image_instruction = "What do you see in the image? Be specific and descriptive"

    @classmethod
    def from_args(cls):
        parser = argparse.ArgumentParser(description="Image Indexer")
        parser.add_argument("directory", help="Directory containing the files")
        parser.add_argument("--api-url", default="http://localhost:5001", help="URL for the LLM API")
        parser.add_argument("--api-password", default="", help="Password for the LLM API")
        parser.add_argument("--no-crawl", action="store_true", help="Disable recursive indexing")
        parser.add_argument("--force-rehash", action="store_true", help="Force rehashing of all files")
        parser.add_argument("--overwrite", action="store_true", help="Overwrite existing file metadata without making backup")
        parser.add_argument("--dry-run", action="store_true", help="Don't write any files")
        parser.add_argument("--write-keywords", action="store_true", help="Write Keywords metadata")
        parser.add_argument("--write-title", action="store_true", help="Write Title metadata")
        parser.add_argument("--write-subject", action="store_true", help="Write Subject metadata")
        parser.add_argument("--write-description", action="store_true", help="Write Description metadata")
        parser.add_argument("--write-caption", action="store_true", help="Write Caption metadata")
        parser.add_argument("--image-instruction", default="What do you see in the image? Be specific and descriptive", help="Custom instruction for image description")
        
        args = parser.parse_args()
        
        config = cls()
        config.directory = args.directory
        config.api_url = args.api_url
        config.api_password = args.api_password
        config.no_crawl = args.no_crawl
        config.force_rehash = args.force_rehash
        config.overwrite = args.overwrite
        config.dry_run = args.dry_run
        config.write_keywords = args.write_keywords
        config.write_title = args.write_title
        config.write_subject = args.write_subject
        config.write_description = args.write_description
        config.write_caption = args.write_caption
        config.image_instruction = args.image_instruction
        
        return config


def clean_string(data):
    if isinstance(data, dict):
        data = json.dumps(data)
    if isinstance(data, str):
        data = re.sub(r"\n", "", data)
        data = re.sub(r'["""]', '"', data)
        data = re.sub(r"\\{2}", "", data)
        last_period = data.rfind('.')
        if last_period != -1:
            data = data[:last_period+1]
    return data

def clean_json(data):
    if data is None:
        return ""
    if isinstance(data, dict):
        data = json.dumps(data)
        try:
            return json.loads(data)
        except:
            pass
    pattern = r"```json\s*(.*?)\s*```"
    match = re.search(pattern, data, re.DOTALL)

    if match:
        json_str = match.group(1).strip()
        data = json_str
    else:
        json_str = re.search(r"\{.*\}", data, re.DOTALL)
        if json_str:
            data = json_str.group(0)

    data = re.sub(r"\n", " ", data)
    data = re.sub(r'["""]', '"', data)

    try:
        return json.loads(repair_json(data))
    except json.JSONDecodeError:
        print("JSON error")
    return data


class FileProcessor:
    def __init__(self, config, llm_processor):
        self.config = config
        self.llm_processor = llm_processor
        self.max_megapixels = 16
        self.target_megapixels = 8

    def process_file(self, file_path, exif_metadata, mime_type):
        self.mime_type = mime_type
        image_data = self.prepare_image_for_api(file_path)
        if image_data is None:
            print(f"Skipping {file_path}: Not a supported image type")
            return None

        self.caption = clean_string(self.llm_processor.interrogate_image(image_data))
        description = self.create_metadata_prompt(exif_metadata, self.caption)
        instruction = self.llm_processor.metadata_instruction
        llm_metadata = clean_json(
            self.llm_processor.describe_content(
                instruction=instruction, content=description
            )
        )

        self.update_xmp_tags(file_path, llm_metadata)
        return {"llm_metadata": llm_metadata, "Caption": self.caption}

    """Looks at each file to determine if it is an image by mimetype.

    If so it will check if it is JPEG or PNG, and if not looks for an embedded
    jpeg file (as in RAW files).
    """

    def prepare_image_for_api(self, file_path):
        if self.mime_type in ['image/jpeg', 'image/png']:
            return file_path
        else:
            with exiftool.ExifToolHelper() as et:
                metadata = et.get_metadata(file_path)[0]
                if 'JPEG:JPEGInterchangeFormat' in metadata:
                    embedded_jpeg = et.get_embedded_jpeg(file_path)
                    if embedded_jpeg:
                        return io.BytesIO(embedded_jpeg)
            return self.process_image(file_path)
        return None

    """Scales the image down if it is really big, then converts to
    
    RGB then JPEG. It is kept in memory the whole time to avoid writing to disk
    """    

    def process_image(self, file_path):
        with Image.open(file_path) as img:
            width, height = img.size
            megapixels = (width * height) / 1_000_000

            if megapixels > self.max_megapixels:
                scale_factor = (self.target_megapixels / megapixels) ** 0.5
                new_width = int(width * scale_factor)
                new_height = int(height * scale_factor)
                img = img.resize((new_width, new_height), Image.LANCZOS)

            if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                img = img.convert('RGB')

            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=95)
            buffer.seek(0)
            return buffer
            
    def update_xmp_tags(self, file_path, llm_metadata):
        try:
            with exiftool.ExifToolHelper() as et:
                xmp_metadata = {}
                if self.config.write_keywords and "Keywords" in llm_metadata:
                    xmp_metadata["IPTC:Keywords"] = llm_metadata["Keywords"]
                
                if self.config.write_title and "Title" in llm_metadata:
                    xmp_metadata["XMP-dc:Title"] = llm_metadata["Title"]
                
                if self.config.write_subject and "Subject" in llm_metadata:
                    xmp_metadata["XMP-dc:Subject"] = llm_metadata["Subject"]
                
                if self.config.write_description and "Summary" in llm_metadata:
                    xmp_metadata["XMP-dc:Description"] = llm_metadata["Summary"]
                    
                if self.config.write_caption and self.caption:
                    xmp_metadata["Caption"] = self.caption
              
                if not self.config.dry_run:
                    if self.config.overwrite:
                        et.set_tags(
                            file_path,
                            tags=xmp_metadata,
                            params=["-P", "-overwrite_original"],
                        )
                    else:
                        et.set_tags(file_path, tags=xmp_metadata)

                    print(f"Updated XMP tags for {file_path}")
                else:
                    print(f"Dry run, {file_path} not updated")

        except Exception as e:
            print(f"Error updating XMP tags for {file_path}: {str(e)}")


    def extract_basic_metadata(self, file_path, root_dir):
        path = pathlib.Path(file_path)
        stats = path.stat()

        return {
            "filename": path.name,
            "relative_path": str(path.relative_to(root_dir)),
            "size": stats.st_size,
            "created": time.ctime(stats.st_ctime),
            "modified": time.ctime(stats.st_mtime),
            "extension": path.suffix.lower(),
            "file_hash": self._calculate_file_hash(file_path),
        }

    @staticmethod
    def _calculate_file_hash(file_path):
        xxh = xxhash.xxh64()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                xxh.update(chunk)

        return xxh.hexdigest()

    def extract_exif_metadata(self, file_path):
        try:
            with exiftool.ExifToolHelper() as et:
                metadata = et.get_metadata(file_path)[0]

                return {
                    "exif_metadata": {
                        k: v
                        for k, v in metadata.items()
                        if not isinstance(v, (bytes, bytearray)) and len(str(v)) < 1000
                    }
                }
        except Exception as e:
            print(f"ExifTool extraction failed for {file_path}: {str(e)}")

            return {}

    def create_metadata_prompt(self, exif_metadata, caption):
        prompt = "Metadata:\n"
        clean_metadata = {}
        
        for key, value in exif_metadata.get("exif_metadata", {}).items():
            clean_key = key.split(':')[-1]
            clean_metadata[clean_key] = value
            if clean_key not in ['Keywords', 'Description', 'Title', 'Subject', 'Caption']:
                prompt += f"{clean_key} is {value}\n"
        
        if caption:
            prompt += f"\nCaption: {caption}"
        
        return prompt.rstrip()


class DatabaseHandler:
    def __init__(self, db_path):
        self.db = TinyDB(db_path)
        
    def insert_or_update(self, metadata):
        File = Query()
        self.db.upsert(metadata, File.relative_path == metadata["relative_path"])

    def file_needs_update(self, file_path, file_mtime):
        File = Query()
        relative_path = str(pathlib.Path(file_path))

        result = self.db.search(File.relative_path == os.path.basename(relative_path))

        if not result:

            return True

        return False


class IndexManager:
    def __init__(self, config, db_handler, file_processor):
        self.config = config
        self.db_handler = db_handler
        self.file_processor = file_processor

    def crawl_directory(self):
        if not self.config.no_crawl:
            for dirpath, _, filenames in os.walk(self.config.directory):
                for filename in filenames:
                    yield os.path.join(dirpath, filename)
        else:
            for filename in os.listdir(self.config.directory):
                file_path = os.path.join(self.config.directory, filename)
                if os.path.isfile(file_path):
                    yield file_path
                    
    def index_files(self, check_paused_or_stopped):
        for file_path in self.crawl_directory():
            if check_paused_or_stopped:
                check_paused_or_stopped()

            mime_type, _ = mimetypes.guess_type(file_path)
            
            if mime_type and mime_type.startswith('image/'):
                file_mtime = os.path.getmtime(file_path)
                
                if self.config.force_rehash or self.db_handler.file_needs_update(file_path, file_mtime):
                    basic_metadata = self.file_processor.extract_basic_metadata(file_path, self.config.directory)
                    exif_metadata = self.file_processor.extract_exif_metadata(file_path)
                    
                    processed_metadata = self.file_processor.process_file(file_path, exif_metadata, mime_type)
                    
                    if processed_metadata is not None:
                        combined_metadata = {**basic_metadata, **exif_metadata, **processed_metadata}
                        if not self.config.dry_run:
                            self.db_handler.insert_or_update(combined_metadata)
                        yield combined_metadata
                    else:
                        print(f"Skipping {file_path}: File processing failed")
                else:
                    print(f"Skipping {file_path}: Up to date")
            else:
                print(f"Skipping {file_path}: Not an image file")
                
def main(config=None, callback=None, check_paused_or_stopped=None):
    if config is None:
        config = Config.from_args()
    db_file = os.path.join(config.directory, "filedata.json")
    
    llm_processor = LLMProcessor(config)
    file_processor = FileProcessor(config, llm_processor)
    db_handler = DatabaseHandler(db_file)
    index_manager = IndexManager(config, db_handler, file_processor)

    def output_handler(message):
        print(message)  # Always print to console
        if callback:
            callback(message)  # Send to GUI if callback is provided

    try:
        for metadata in index_manager.index_files(check_paused_or_stopped):
            if "llm_metadata" in metadata:
                output_message = f"File: {metadata.get('filename', 'Unknown')}\n"
                if config.write_title and 'Title' in metadata['llm_metadata']:
                    output_message += f"Title: {metadata['llm_metadata']['Title']}\n"
                if config.write_subject and 'Subject' in metadata['llm_metadata']:
                    output_message += f"Subject: {metadata['llm_metadata']['Subject']}\n"
                if config.write_keywords and 'Keywords' in metadata['llm_metadata']:
                    output_message += f"Keywords: {metadata['llm_metadata']['Keywords']}\n"
                if config.write_caption:
                    output_message += f"Caption: {metadata.get('Caption', 'N/A')}\n"
                if config.write_description and 'Summary' in metadata['llm_metadata']:
                    output_message += f"Description: {metadata['llm_metadata']['Summary']}\n"
                output_message += "\n"  # Add a blank line between files
                output_handler(output_message)
    except Exception as e:
        output_handler(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    main()
