import os
import sys
import warnings

FILE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(FILE_DIR)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)
warnings.filterwarnings('ignore')

import cv2
import gradio as gr
import random
import numpy as np
from PIL import Image

import torch

from simpleAICV.interactive_segmentation.models import segment_anything
from simpleAICV.interactive_segmentation.common import load_state_dict

seed = 0
model_name = 'sam_h'
trained_model_path = '/root/autodl-tmp/pretrained_models/sam_official_pytorch_weights/sam_vit_h_4b8939.pth'
input_image_size = 1024
clip_threshold = 0.5

os.environ['PYTHONHASHSEED'] = str(seed)
random.seed(seed)
np.random.seed(seed)
torch.manual_seed(seed)

model = segment_anything.__dict__[model_name](**{
    'image_size': input_image_size,
    'use_gradient_checkpoint': False,
    'frozen_image_encoder': False,
    'frozen_prompt_encoder': False,
    'frozen_mask_decoder': False,
    'sigmoid_out': False,
    'binary_mask_out': True,
    'mask_threshold': 0.0,
})
if trained_model_path:
    load_state_dict(trained_model_path, model)
else:
    print('No pretrained model load!')
model.eval()


def preprocess_image(image, resize):
    # PIL image(RGB) to opencv image(RGB)
    image = np.asarray(image).astype(np.float32)

    origin_image = image.copy()
    h, w, _ = origin_image.shape

    origin_size = [h, w]

    factor = resize / max(h, w)

    resize_h, resize_w = int(round(h * factor)), int(round(w * factor))
    image = cv2.resize(image, (resize_w, resize_h))

    # normalize
    mean = [123.675, 116.28, 103.53]
    std = [58.395, 57.12, 57.375]
    image = (image - mean) / std

    padded_img = np.zeros(
        (max(resize_h, resize_w), max(resize_h, resize_w), 3),
        dtype=np.float32)
    padded_img[:resize_h, :resize_w, :] = image

    scale = factor
    scaled_size = [resize_h, resize_w]

    return origin_image, padded_img, scale, scaled_size, origin_size


def predict(inputs, mask_out_idx):
    image = inputs['image']
    origin_image, resized_img, scale, scaled_size, origin_size = preprocess_image(
        image, input_image_size)
    resized_img = torch.tensor(resized_img).permute(2, 0, 1).unsqueeze(0)

    mask = inputs['mask']
    mask = cv2.cvtColor(mask, cv2.COLOR_RGB2GRAY)
    mask[mask > 0] = 255

    # 获取最小外接矩形坐标
    x1, y1, w, h = cv2.boundingRect(mask)
    x2 = x1 + w
    y2 = y1 + h

    input_box = np.array([x1, y1, x2, y2]) * scale
    input_prompt_box = torch.tensor(np.expand_dims(input_box,
                                                   axis=0)).float().cuda()

    batch_prompts = {
        'prompt_point': None,
        'prompt_box': input_prompt_box,
        'prompt_mask': None
    }

    mask_out_idx = [mask_out_idx]

    with torch.no_grad():
        batch_mask_outputs, batch_iou_outputs = model(
            resized_img, batch_prompts, mask_out_idxs=mask_out_idx)
        masks, iou_preds = batch_mask_outputs, batch_iou_outputs

    masks = masks.squeeze(dim=0).squeeze(dim=0)
    masks = masks.numpy().astype(np.float32)
    masks = masks[:scaled_size[0], :scaled_size[1]]

    iou_preds = iou_preds.squeeze(dim=0).squeeze(dim=0)
    iou_preds = iou_preds.numpy()

    masks = cv2.resize(masks, (origin_size[1], origin_size[0]))
    masks[masks < clip_threshold] = 0
    masks[masks >= clip_threshold] = 1

    binary_mask = (masks.copy() * 255.).astype('uint8')

    origin_image = cv2.cvtColor(origin_image, cv2.COLOR_RGB2BGR)
    origin_image = origin_image.astype('uint8')

    masks_class_color = list(np.random.choice(range(256), size=3))

    per_image_mask = np.zeros(
        (origin_image.shape[0], origin_image.shape[1], 3))

    per_image_contours = []
    per_mask = masks

    per_mask_color = np.array(
        (masks_class_color[0], masks_class_color[1], masks_class_color[2]))

    per_object_mask = np.nonzero(per_mask == 1.)
    per_image_mask[per_object_mask[0], per_object_mask[1]] = per_mask_color

    # get contours
    new_per_image_mask = np.zeros(
        (origin_image.shape[0], origin_image.shape[1]))
    new_per_image_mask[per_object_mask[0], per_object_mask[1]] = 255
    contours, _ = cv2.findContours(new_per_image_mask.astype('uint8'),
                                   cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    per_image_contours.append(contours)

    per_image_mask = per_image_mask.astype('uint8')
    per_image_mask = cv2.cvtColor(per_image_mask, cv2.COLOR_RGBA2BGR)

    all_object_mask = np.nonzero(per_image_mask != 0)
    per_image_mask[all_object_mask[0], all_object_mask[1]] = cv2.addWeighted(
        origin_image[all_object_mask[0], all_object_mask[1]], 0.5,
        per_image_mask[all_object_mask[0], all_object_mask[1]], 1, 0)
    no_class_mask = np.nonzero(per_image_mask == 0)
    per_image_mask[no_class_mask[0],
                   no_class_mask[1]] = origin_image[no_class_mask[0],
                                                    no_class_mask[1]]
    for contours in per_image_contours:
        cv2.drawContours(per_image_mask, contours, -1, (255, 255, 255), 1)

    per_image_mask = cv2.cvtColor(per_image_mask, cv2.COLOR_BGR2RGB)
    per_image_mask = Image.fromarray(np.uint8(per_image_mask))

    return per_image_mask, binary_mask


with gr.Blocks() as demo:
    with gr.Tab(label='Segment Anything!'):
        with gr.Row():
            with gr.Column():
                inputs = gr.Image(label="Circle the target",
                                  tool="sketch",
                                  type='numpy',
                                  image_mode='RGB',
                                  mask_opacity=0.5,
                                  brush_radius=30)
                with gr.Row():
                    gr.Markdown('Choose sam model mask out idx.')
                    mask_out_idx = gr.Slider(minimum=0,
                                             maximum=3,
                                             value=0,
                                             step=1,
                                             label='mask out idx')

                # run button
                run_button = gr.Button("RUN!")
            # show image with mask
            with gr.Tab(label='Image with Mask'):
                output_image_with_mask = gr.Image(type='pil')
            # only show mask
            with gr.Tab(label='Mask'):
                output_mask = gr.Image(type='pil')

    run_button.click(predict,
                     inputs=[inputs, mask_out_idx],
                     outputs=[output_image_with_mask, output_mask])

# local website: http://127.0.0.1:6006/
demo.queue().launch(share=True,
                    server_name='0.0.0.0',
                    server_port=6006,
                    show_error=True)
