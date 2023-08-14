import torch
from torchvision import transforms
import json
from urllib.request import urlopen

class_names = None

def get_raw_tags(model, input_image):
    global class_names

    if class_names is None:
        print('Loading class names...')
        with urlopen("https://github.com/RF5/danbooru-pretrained/raw/master/config/class_names_6000.json") as url:
            class_names = json.loads(url.read().decode())
        print('Done loading class names')
    else:
        print('Class names already loaded')

    preprocess = transforms.Compose([
        transforms.Resize(360),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.7137, 0.6628, 0.6519], std=[0.2970, 0.3017, 0.2979]),
    ])

    try:
        if input_image.mode != 'RGB':
            input_image = input_image.convert('RGB')
    except Exception as e:
        print('Image conversion failed')
        print(e)

    input_tensor = preprocess(input_image)
    input_batch = input_tensor.unsqueeze(0)

    model.eval()

    with torch.no_grad():
        output = model(input_batch)

    probs = torch.sigmoid(output[0]).cpu()

    return probs