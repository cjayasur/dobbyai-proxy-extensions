#!/usr/bin/env python3
"""OpenAI-compatible vision model server for Qwen3-VL.

Serves a single purpose: describe images for the two-stage VL→Coder pipeline.
The proxy on tower sends images here, gets text descriptions back, then forwards
the descriptions to Coder-30B for code generation.

Usage:
    python vision_server.py --model unsloth/Qwen3-VL-8B-Instruct-unsloth-bnb-4bit --port 8005
"""

import argparse
import base64
import io
import threading
import time
import uuid

import torch
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Any, Union
from PIL import Image
from transformers import Qwen3VLForConditionalGeneration, AutoProcessor, BitsAndBytesConfig
from qwen_vl_utils import process_vision_info

app = FastAPI(docs_url=None, redoc_url=None)

# Globals
model = None
processor = None
model_name = ""
device = "cuda:0"
max_context = 4096
generate_lock = threading.Lock()


# ---------- Request schemas (OpenAI vision format) ----------

class ChatCompletionRequest(BaseModel):
    model: str = ""
    messages: list[dict[str, Any]]
    temperature: float = 0.1
    max_tokens: int = 1024


# ---------- Endpoints ----------

@app.get("/health")
def health():
    gpu_idx = int(device.split(":")[-1]) if ":" in device else 0
    props = torch.cuda.get_device_properties(gpu_idx)
    mem_used = torch.cuda.memory_allocated(gpu_idx) / 1024**3
    mem_total = props.total_memory / 1024**3
    return {
        "status": "ok",
        "model": model_name,
        "device": device,
        "gpu": props.name,
        "gpu_memory_used_gb": round(mem_used, 2),
        "gpu_memory_total_gb": round(mem_total, 2),
    }


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [{"id": model_name, "object": "model", "owned_by": "local"}]
    }


def decode_image_from_content(content_block: dict) -> Union[Image.Image, None]:
    """Extract PIL Image from OpenAI image_url content block."""
    image_url = content_block.get("image_url", {})
    url = image_url.get("url", "")

    if url.startswith("data:"):
        # base64 data URI: data:image/png;base64,iVBOR...
        header, b64data = url.split(",", 1)
        img_bytes = base64.b64decode(b64data)
        return Image.open(io.BytesIO(img_bytes)).convert("RGB")
    else:
        # URL — download it
        import httpx
        resp = httpx.get(url, timeout=30)
        return Image.open(io.BytesIO(resp.content)).convert("RGB")


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest):
    """Handle OpenAI-format vision chat completion."""

    # Convert OpenAI messages to Qwen VL format
    qwen_messages = []
    for msg in req.messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            qwen_messages.append({"role": role, "content": [{"type": "text", "text": content}]})
        elif isinstance(content, list):
            qwen_content = []
            for block in content:
                if block.get("type") == "text":
                    qwen_content.append({"type": "text", "text": block.get("text", "")})
                elif block.get("type") == "image_url":
                    # Decode base64 image to temp file for qwen_vl_utils
                    img = decode_image_from_content(block)
                    if img:
                        # Save to temp path for the processor
                        tmp_path = f"/tmp/vl_input_{uuid.uuid4().hex[:8]}.png"
                        img.save(tmp_path)
                        qwen_content.append({"type": "image", "image": tmp_path})
            qwen_messages.append({"role": role, "content": qwen_content})

    # Apply chat template
    text = processor.apply_chat_template(qwen_messages, tokenize=False, add_generation_prompt=True)
    image_inputs, video_inputs = process_vision_info(qwen_messages)

    inputs = processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
    ).to(device)

    input_len = inputs["input_ids"].shape[1]

    max_new = min(req.max_tokens, max_context - input_len)
    if max_new <= 0:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": f"Input too long ({input_len} tokens)", "type": "invalid_request_error"}},
        )

    t0 = time.time()
    with generate_lock:
        with torch.no_grad():
            output_ids = model.generate(
                **inputs,
                max_new_tokens=max_new,
                temperature=max(req.temperature, 0.01),
                do_sample=req.temperature > 0,
            )
    elapsed = time.time() - t0

    # Decode only new tokens
    new_ids = output_ids[0][input_len:]
    text_out = processor.decode(new_ids, skip_special_tokens=True)
    output_len = len(new_ids)

    # Clean up temp images
    import glob, os
    for f in glob.glob("/tmp/vl_input_*.png"):
        try:
            os.remove(f)
        except:
            pass

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "model": req.model or model_name,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text_out},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": input_len,
            "completion_tokens": output_len,
            "total_tokens": input_len + output_len,
        },
        "x_timing": {"total_seconds": round(elapsed, 2)},
    }


# ---------- Startup ----------

def load_model(model_id: str, device_str: str):
    global model, processor, model_name, device
    model_name = model_id
    device = device_str

    print(f"Loading processor: {model_id}")
    processor = AutoProcessor.from_pretrained(model_id)

    print(f"Loading model: {model_id} (4-bit) on {device}")
    quant_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    model = Qwen3VLForConditionalGeneration.from_pretrained(
        model_id,
        quantization_config=quant_config,
        device_map=device,
        torch_dtype=torch.bfloat16,
    )
    model.eval()
    print(f"Model loaded. VRAM: {torch.cuda.memory_allocated() / 1024**3:.1f} GB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenAI-compatible vision model server")
    parser.add_argument("--model", required=True, help="HuggingFace model ID")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--port", type=int, default=8005)
    parser.add_argument("--max-length", type=int, default=4096)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    max_context = args.max_length
    load_model(args.model, args.device)
    uvicorn.run(app, host=args.host, port=args.port)
