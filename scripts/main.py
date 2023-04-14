from io import BytesIO
import string
import gradio as gr
import requests
from caption_anything import CaptionAnything
import torch
import json
from diffusers import StableDiffusionInpaintPipeline
import sys
import argparse
from model import parse_augment
import numpy as np
import PIL.ImageDraw as ImageDraw
from image_editing_utils import create_bubble_frame
import copy
from tools import mask_painter
from PIL import Image
import os
import cv2

import os
import io
import json
import numpy as np
import cv2

import gradio as gr

import modules.scripts as scripts
from modules import script_callbacks
from modules.shared import opts
from modules.paths import models_path


def download_checkpoint(url, folder, filename):
    os.makedirs(folder, exist_ok=True)
    filepath = os.path.join(folder, filename)

    if not os.path.exists(filepath):
        response = requests.get(url, stream=True)
        with open(filepath, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

    return filepath

checkpoint_url = "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth"
folder = "segmenter"
filename = "sam_vit_h_4b8939.pth"

download_checkpoint(checkpoint_url, folder, filename)


title = """<h1 align="center">Edit Anything</h1>"""
description = """Gradio demo for Segment Anything, image to dense Segment generation with various language styles. To use it, simply upload your image, or click one of the examples to load them. 
"""

examples = [
    ["test_img/img35.webp"],
    ["test_img/img2.jpg"],
    ["test_img/img5.jpg"],
    ["test_img/img12.jpg"],
    ["test_img/img14.jpg"],
    ["test_img/img0.png"],
    ["test_img/img1.jpg"],
]

args = parse_augment()
# args.device = 'cuda:5'
# args.disable_gpt = False
# args.enable_reduce_tokens = True
# args.port=20322
model = CaptionAnything(args)

def init_openai_api_key(api_key):
    # os.environ['OPENAI_API_KEY'] = api_key
    model.init_refiner(api_key)
    openai_available = model.text_refiner is not None
    return gr.update(visible = openai_available), gr.update(visible = openai_available), gr.update(visible = openai_available), gr.update(visible = True), gr.update(visible = True)

def get_prompt(chat_input, click_state):    
    points = click_state[0]
    labels = click_state[1]
    inputs = json.loads(chat_input)
    for input in inputs:
        points.append(input[:2])
        labels.append(input[2])
    
    prompt = {
        "prompt_type":["click"],
        "input_point":points,
        "input_label":labels,
        "multimask_output":"True",
    }
    return prompt

def chat_with_points(chat_input, click_state, state, mask_save_path,image_input):
    
    points, labels, captions = click_state
    
    
    # inpainting
    pipe = StableDiffusionInpaintPipeline.from_pretrained(
        "stabilityai/stable-diffusion-2-inpainting",
        torch_dtype=torch.float32,
    )


    pipe = pipe.to("cuda")
#     mask = cv2.imread(mask_save_path)
    mask = mask_save_path
    image_input = np.array(image_input)
    h,w = image_input.shape[:2]
    
    image = cv2.resize(image_input,(512,512))
    mask = cv2.resize(mask,(512,512)).astype(np.uint8)
    print(image.shape,mask.shape)
    print("chat_input:",chat_input)
    image = pipe(prompt=chat_input, image=image, mask_image=mask).images[0]
    image = image.resize((w,h))
    
#     image = Image.fromarray(image, mode='RGB')
    return state, state, image

def inference_seg_cap(image_input, point_prompt, language, sentiment, factuality, length, state, click_state, evt:gr.SelectData):

    if point_prompt == 'Positive':
        coordinate = "[[{}, {}, 1]]".format(str(evt.index[0]), str(evt.index[1]))
    else:
        coordinate = "[[{}, {}, 0]]".format(str(evt.index[0]), str(evt.index[1]))
        
    controls = {'length': length,
             'sentiment': sentiment,
             'factuality': factuality,
             'language': language}

    # click_coordinate = "[[{}, {}, 1]]".format(str(evt.index[0]), str(evt.index[1])) 
    # chat_input = click_coordinate
    prompt = get_prompt(coordinate, click_state)
    print('prompt: ', prompt, 'controls: ', controls)

    out = model.inference(image_input, prompt, controls)
    state = state + [(None, "Image point: {}, Input label: {}".format(prompt["input_point"], prompt["input_label"]))]
    # for k, v in out['generated_captions'].items():
    #     state = state + [(f'{k}: {v}', None)]
#     state = state + [("caption: {}".format(out['generated_captions']['raw_caption']), None)]
#     wiki = out['generated_captions'].get('wiki', "")
#     click_state[2].append(out['generated_captions']['raw_caption'])
    
#     text = out['generated_captions']['raw_caption']
    # draw = ImageDraw.Draw(image_input)
    # draw.text((evt.index[0], evt.index[1]), text, textcolor=(0,0,255), text_size=120)
    input_mask = np.array(out['mask'].convert('P'))
    image_input = mask_painter(np.array(image_input), input_mask)
    origin_image_input = image_input
    text = "edit"
    image_input = create_bubble_frame(image_input, text, (evt.index[0], evt.index[1]))

    yield state, state, click_state, image_input, input_mask
#     if not args.disable_gpt and model.text_refiner:
#         refined_caption = model.text_refiner.inference(query=text, controls=controls, context=out['context_captions'])
#         # new_cap = 'Original: ' + text + '. Refined: ' + refined_caption['caption']
#         new_cap = refined_caption['caption']
#         refined_image_input = create_bubble_frame(origin_image_input, new_cap, (evt.index[0], evt.index[1]))
#         yield state, state, click_state, chat_input, refined_image_input, wiki


def upload_callback(image_input, state):
    state = [] + [('Image size: ' + str(image_input.size), None)]
    click_state = [[], [], []]
    res = 1024
    width, height = image_input.size
    ratio = min(1.0 * res / max(width, height), 1.0)
    if ratio < 1.0:
        image_input = image_input.resize((int(width * ratio), int(height * ratio)))
        print('Scaling input image to {}'.format(image_input.size))
    model.segmenter.image = None
    model.segmenter.image_embedding = None
    model.segmenter.set_image(image_input)
    return state, image_input, click_state, image_input

def on_ui_tabs():
    with gr.Blocks(
        css='''
        #image_upload{min-height:400px}
        #image_upload [data-testid="image"], #image_upload [data-testid="image"] > div{min-height: 600px}
        '''
    ) as SAM:
        state = gr.State([])
        click_state = gr.State([[],[],[]])
        origin_image = gr.State(None)
        mask_save_path = gr.State(None)

        gr.Markdown(title)
        gr.Markdown(description)

        with gr.Row():
            with gr.Column(scale=1.0):
                with gr.Column(visible=True) as modules_not_need_gpt:
                    image_input = gr.Image(type="pil", interactive=True, elem_id="image_upload")
    #                 o_image_input = gr.Image(type="pil", interactive=True, elem_id="image_upload")
                    example_image = gr.Image(type="pil", interactive=False, visible=False)
                    with gr.Row(scale=1.0):
                        point_prompt = gr.Radio(
                            choices=["Positive",  "Negative"],
                            value="Positive",
                            label="Point Prompt",
                            interactive=True)
                        clear_button_clike = gr.Button(value="Clear Clicks", interactive=True)
                        clear_button_image = gr.Button(value="Clear Image", interactive=True)
                with gr.Column(visible=True) as modules_need_gpt:
                    with gr.Row(scale=1.0):
                        language = gr.Dropdown(['English', 'Chinese', 'French', "Spanish", "Arabic", "Portuguese", "Cantonese"], value="English", label="Language", interactive=True)

                        sentiment = gr.Radio(
                            choices=["Positive", "Natural", "Negative"],
                            value="Natural",
                            label="Sentiment",
                            interactive=True,
                        )
                    with gr.Row(scale=1.0):
                        factuality = gr.Radio(
                            choices=["Factual", "Imagination"],
                            value="Factual",
                            label="Factuality",
                            interactive=True,
                        )
                        length = gr.Slider(
                            minimum=10,
                            maximum=80,
                            value=10,
                            step=1,
                            interactive=True,
                            label="Length",
                        )

            with gr.Column(scale=0.5):
    #             openai_api_key = gr.Textbox(
    #                 placeholder="Input openAI API key and press Enter (Input blank will disable GPT)",
    #                 show_label=False,
    #                 label = "OpenAI API Key",
    #                 lines=1,
    #                 type="password"
    #                 )
    #             with gr.Column(visible=True) as modules_need_gpt2:
    #                 wiki_output = gr.Textbox(lines=6, label="Wiki")
                with gr.Column(visible=True) as modules_not_need_gpt2:
                    chatbot = gr.Chatbot(label="History",).style(height=450,scale=0.5)
                    with gr.Column(visible=True) as modules_need_gpt3:
                        chat_input = gr.Textbox(lines=1, label="Edit Prompt")
                        with gr.Row():
                            clear_button_text = gr.Button(value="Clear Text", interactive=True)
                            submit_button_text = gr.Button(value="Submit", interactive=True, variant="primary")

    #     openai_api_key.submit(init_openai_api_key, inputs=[openai_api_key], outputs=[modules_need_gpt,modules_need_gpt2, modules_need_gpt3, modules_not_need_gpt, modules_not_need_gpt2])

        clear_button_clike.click(
            lambda x: ([[], [], []], x, ""),
            [origin_image],
            [click_state, image_input],
            queue=False,
            show_progress=False
        )

        clear_button_image.click(
            lambda: (None, [], [], [[], [], []], "", ""),
            [],
            [image_input, chatbot, state, click_state, origin_image],
            queue=False,
            show_progress=False
        )
        clear_button_text.click(
            lambda: ([], [], [[], [], []]),
            [],
            [chatbot, state, click_state],
            queue=False,
            show_progress=False
        )


        image_input.clear(
            lambda: (None, [], [], [[], [], []], "", ""),
            [],
            [image_input, chatbot, state, click_state, origin_image],
            queue=False,
            show_progress=False
        )

        def example_callback(x):
            model.image_embedding = None
            return x

    #     gr.Examples(
    #         examples=examples,
    #         inputs=[example_image],
    #     )

        submit_button_text.click(
            chat_with_points,
            [chat_input, click_state, state, mask_save_path,origin_image],
            [chatbot, state, image_input]
        )


        image_input.upload(upload_callback,[image_input, state], [state, origin_image, click_state, image_input])
        chat_input.submit(chat_with_points, [chat_input, click_state, state, mask_save_path, origin_image], [chatbot, state, image_input])
        example_image.change(upload_callback,[example_image, state], [state, origin_image, click_state, image_input])

        # select coordinate
        image_input.select(inference_seg_cap, 
            inputs=[
            origin_image,
            point_prompt,
            language,
            sentiment,
            factuality,
            length,
            state,
            click_state
            ],
            outputs=[chatbot, state, click_state, image_input ,mask_save_path],
            show_progress=False, queue=True)
        
    return [(SAM, "Segment Anything", "segment_anything")]


script_callbacks.on_ui_tabs(on_ui_tabs)