import runpod
from runpod.serverless.utils import rp_upload
import json
import urllib.request
import urllib.parse
import time
import os
import requests
import base64
from io import BytesIO

# Time to wait between API check attempts in milliseconds
COMFY_API_AVAILABLE_INTERVAL_MS = 50
# Maximum number of API check attempts
COMFY_API_AVAILABLE_MAX_RETRIES = 5000
# Time to wait between poll attempts in milliseconds
COMFY_POLLING_INTERVAL_MS = int(
    os.environ.get("COMFY_POLLING_INTERVAL_MS", 250))
# Maximum number of poll attempts
COMFY_POLLING_MAX_RETRIES = int(
    os.environ.get("COMFY_POLLING_MAX_RETRIES", 500))
# Host where ComfyUI is running
COMFY_HOST = "127.0.0.1:8188"
# Enforce a clean state after each job is done
# see https://docs.runpod.io/docs/handler-additional-controls#refresh-worker
REFRESH_WORKER = os.environ.get("REFRESH_WORKER", "false").lower() == "true"

MIN_WORKFLOW = """{"6":{"inputs":{"text":"","clip":["57",1]},"class_type":"CLIPTextEncode","_meta":{"title":"CLIP Text Encode (Positive Prompt)"}},"8":{"inputs":{"samples":["13",0],"vae":["10",0]},"class_type":"VAEDecode","_meta":{"title":"VAE Decode"}},"9":{"inputs":{"filename_prefix":"ComfyUI","images":["8",0]},"class_type":"SaveImage","_meta":{"title":"Save Image"}},"10":{"inputs":{"vae_name":"ae.safetensors"},"class_type":"VAELoader","_meta":{"title":"Load VAE"}},"11":{"inputs":{"clip_name1":"t5xxl_fp8_e4m3fn.safetensors","clip_name2":"clip_l.safetensors","type":"flux","device":"default"},"class_type":"DualCLIPLoader","_meta":{"title":"DualCLIPLoader"}},"12":{"inputs":{"unet_name":"flux1-dev-fp8.safetensors","weight_dtype":"default"},"class_type":"UNETLoader","_meta":{"title":"Load Diffusion Model"}},"13":{"inputs":{"noise":["25",0],"guider":["22",0],"sampler":["16",0],"sigmas":["17",0],"latent_image":["27",0]},"class_type":"SamplerCustomAdvanced","_meta":{"title":"SamplerCustomAdvanced"}},"16":{"inputs":{"sampler_name":"euler"},"class_type":"KSamplerSelect","_meta":{"title":"KSamplerSelect"}},"17":{"inputs":{"scheduler":"simple","steps":20,"denoise":1,"model":["39",0]},"class_type":"BasicScheduler","_meta":{"title":"BasicScheduler"}},"22":{"inputs":{"model":["39",0],"conditioning":["26",0]},"class_type":"BasicGuider","_meta":{"title":"BasicGuider"}},"25":{"inputs":{"noise_seed":882489827747891},"class_type":"RandomNoise","_meta":{"title":"RandomNoise"}},"26":{"inputs":{"guidance":3.5,"conditioning":["6",0]},"class_type":"FluxGuidance","_meta":{"title":"CFG"}},"27":{"inputs":{"width":16,"height":16,"batch_size":1},"class_type":"EmptySD3LatentImage","_meta":{"title":"EmptySD3LatentImage"}},"39":{"inputs":{"weight":1,"start_at":0,"end_at":1,"fusion":"mean","fusion_weight_max":1,"fusion_weight_min":0,"train_step":1000,"use_gray":true,"model":["57",0],"pulid_flux":["40",0],"eva_clip":["41",0],"face_analysis":["42",0],"image":["58",0]},"class_type":"ApplyPulidFlux","_meta":{"title":"Apply PuLID Flux"}},"40":{"inputs":{"pulid_file":"pulid_flux_v0.9.1.safetensors"},"class_type":"PulidFluxModelLoader","_meta":{"title":"Load PuLID Flux Model"}},"41":{"inputs":{},"class_type":"PulidFluxEvaClipLoader","_meta":{"title":"Load Eva Clip (PuLID Flux)"}},"42":{"inputs":{"provider":"CPU"},"class_type":"PulidFluxInsightFaceLoader","_meta":{"title":"Load InsightFace (PuLID Flux)"}},"57":{"inputs":{"PowerLoraLoaderHeaderWidget":{"type":"PowerLoraLoaderHeaderWidget"},"lora_1":{"on":true,"lora":"7lu5OFIisO2XIu_HglD5v_pytorch_lora_weights.safetensors","strength":1},"➕ Add Lora":"","model":["12",0],"clip":["11",0]},"class_type":"Power Lora Loader (rgthree)","_meta":{"title":"Power Lora Loader (rgthree)"}},"58":{"inputs":{"data":"iVBORw0KGgoAAAANSUhEUgAAAAgAAAAIAQMAAAD+wSzIAAAABlBMVEX///+/v7+jQ3Y5AAAADklEQVQI12P4AIX8EAgALgAD/aNpbtEAAAAASUVORK5CYII="},"class_type":"LoadImageFromBase64","_meta":{"title":"Load Image From Base64"}}}"""


def validate_input(job_input):
    """
    Validates the input for the handler function.

    Args:
        job_input (dict): The input data to validate.

    Returns:
        tuple: A tuple containing the validated data and an error message, if any.
               The structure is (validated_data, error_message).
    """
    # Validate if job_input is provided
    if job_input is None:
        return None, "Please provide input"

    # Check if input is a string and try to parse it as JSON
    if isinstance(job_input, str):
        try:
            job_input = json.loads(job_input)
        except json.JSONDecodeError:
            return None, "Invalid JSON format in input"

    # Validate 'workflow' in input
    workflow = job_input.get("workflow")
    if workflow is None:
        return None, "Missing 'workflow' parameter"

    # Validate 'images' in input, if provided
    images = job_input.get("images")
    if images is not None:
        if not isinstance(images, list) or not all(
            "name" in image and "image" in image for image in images
        ):
            return (
                None,
                "'images' must be a list of objects with 'name' and 'image' keys",
            )

    # Return validated data and no error
    return {"workflow": workflow, "images": images}, None


def check_server(url, retries=500, delay=50):
    """
    Check if a server is reachable via HTTP GET request

    Args:
    - url (str): The URL to check
    - retries (int, optional): The number of times to attempt connecting to the server. Default is 50
    - delay (int, optional): The time in milliseconds to wait between retries. Default is 500

    Returns:
    bool: True if the server is reachable within the given number of retries, otherwise False
    """

    for i in range(retries):
        try:
            response = requests.get(url)

            # If the response status code is 200, the server is up and running
            if response.status_code == 200:
                print(f"runpod-worker-comfy - API is reachable")
                return True
        except requests.RequestException as e:
            # If an exception occurs, the server may not be ready
            pass

        # Wait for the specified delay before retrying
        time.sleep(delay / 1000)

    print(
        f"runpod-worker-comfy - Failed to connect to server at {url} after {retries} attempts."
    )
    return False


def upload_images(images):
    """
    Upload a list of base64 encoded images to the ComfyUI server using the /upload/image endpoint.

    Args:
        images (list): A list of dictionaries, each containing the 'name' of the image and the 'image' as a base64 encoded string.
        server_address (str): The address of the ComfyUI server.

    Returns:
        list: A list of responses from the server for each image upload.
    """
    if not images:
        return {"status": "success", "message": "No images to upload", "details": []}

    responses = []
    upload_errors = []

    print(f"runpod-worker-comfy - image(s) upload")

    for image in images:
        name = image["name"]
        image_data = image["image"]
        blob = base64.b64decode(image_data)

        # Prepare the form data
        files = {
            "image": (name, BytesIO(blob), "image/png"),
            "overwrite": (None, "true"),
        }

        # POST request to upload the image
        response = requests.post(
            f"http://{COMFY_HOST}/upload/image", files=files)
        if response.status_code != 200:
            upload_errors.append(f"Error uploading {name}: {response.text}")
        else:
            responses.append(f"Successfully uploaded {name}")

    if upload_errors:
        print(f"runpod-worker-comfy - image(s) upload with errors")
        return {
            "status": "error",
            "message": "Some images failed to upload",
            "details": upload_errors,
        }

    print(f"runpod-worker-comfy - image(s) upload complete")
    return {
        "status": "success",
        "message": "All images uploaded successfully",
        "details": responses,
    }


def queue_workflow(workflow):
    """
    Queue a workflow to be processed by ComfyUI

    Args:
        workflow (dict): A dictionary containing the workflow to be processed

    Returns:
        dict: The JSON response from ComfyUI after processing the workflow
    """

    # The top level element "prompt" is required by ComfyUI
    data = json.dumps({"prompt": workflow}).encode("utf-8")

    req = urllib.request.Request(f"http://{COMFY_HOST}/prompt", data=data)
    return json.loads(urllib.request.urlopen(req).read())


def get_history(prompt_id):
    """
    Retrieve the history of a given prompt using its ID

    Args:
        prompt_id (str): The ID of the prompt whose history is to be retrieved

    Returns:
        dict: The history of the prompt, containing all the processing steps and results
    """
    with urllib.request.urlopen(f"http://{COMFY_HOST}/history/{prompt_id}") as response:
        return json.loads(response.read())


def base64_encode(img_path):
    """
    Returns base64 encoded image.

    Args:
        img_path (str): The path to the image

    Returns:
        str: The base64 encoded image
    """
    with open(img_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
        return f"{encoded_string}"


def process_output_images(outputs, job_id):
    """Process generated images and return as S3 URL or base64."""
    COMFY_OUTPUT_PATH = os.environ.get("COMFY_OUTPUT_PATH", "/comfyui/output")
    output_images = []

    for node_id, node_output in outputs.items():
        if "images" in node_output:
            for image in node_output["images"]:
                output_images.append(os.path.join(image["subfolder"], image["filename"]))

    processed_images = []

    for rel_path in output_images:
        local_image_path = f"{COMFY_OUTPUT_PATH}/{rel_path}"
        print(f"Processing image at {local_image_path}")

        if os.path.exists(local_image_path):
            if os.environ.get("BUCKET_ENDPOINT_URL", False):
                image_result = rp_upload.upload_image(job_id, local_image_path)
                print("Image uploaded to AWS S3")
            else:
                image_result = base64_encode(local_image_path)
                print("Image converted to base64")
            processed_images.append(image_result)
        else:
            print(f"Error: Image not found at {local_image_path}")
            processed_images.append(f"Image not found: {local_image_path}")

    return {
        "status": "success",
        "images": processed_images,
    }


def handler(job):
    """
    The main function that handles a job of generating an image.

    This function validates the input, sends a prompt to ComfyUI for processing,
    polls ComfyUI for result, and retrieves generated images.

    Args:
        job (dict): A dictionary containing job details and input parameters.

    Returns:
        dict: A dictionary containing either an error message or a success status with generated images.
    """
    job_input = job["input"]

    # Make sure that the input is valid
    validated_data, error_message = validate_input(job_input)
    if error_message:
        return {"error": error_message}

    # Extract validated data
    workflow = validated_data["workflow"]
    images = validated_data.get("images")

    # Make sure that the ComfyUI API is available
    check_server(
        f"http://{COMFY_HOST}",
        COMFY_API_AVAILABLE_MAX_RETRIES,
        COMFY_API_AVAILABLE_INTERVAL_MS,
    )

    # Upload images if they exist
    upload_result = upload_images(images)

    if upload_result["status"] == "error":
        return upload_result

    # Queue the workflow
    try:
        queued_workflow = queue_workflow(workflow)
        prompt_id = queued_workflow["prompt_id"]
        print(f"runpod-worker-comfy - queued workflow with ID {prompt_id}")
    except Exception as e:
        return {"error": f"Error queuing workflow: {str(e)}"}

    # Poll for completion
    print(f"runpod-worker-comfy - wait until image generation is complete")
    retries = 0
    try:
        while retries < COMFY_POLLING_MAX_RETRIES:
            history = get_history(prompt_id)

            # Exit the loop if we have found the history
            if prompt_id in history and history[prompt_id].get("outputs"):
                break
            else:
                # Wait before trying again
                time.sleep(COMFY_POLLING_INTERVAL_MS / 1000)
                retries += 1
        else:
            return {"error": "Max retries reached while waiting for image generation"}
    except Exception as e:
        return {"error": f"Error waiting for image generation: {str(e)}"}

    # Get the generated image and return it as URL in an AWS bucket or as base64
    images_result = process_output_images(
        history[prompt_id].get("outputs"), job["id"])

    result = {**images_result, "refresh_worker": REFRESH_WORKER}

    return result


# Start the handler only if this script is run directly
if __name__ == "__main__":
    try:
        print("setup - starting handler")
        handler({
            "id": "fake-workflow-id",
            "input": {
                "workflow": json.loads(MIN_WORKFLOW)
            }
        })
        print("setup - handler finished")
    except Exception as e:
        print(f"setup - error: {str(e)}")
    runpod.serverless.start({"handler": handler})
