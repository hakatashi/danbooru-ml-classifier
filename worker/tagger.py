import torch
from torchvision import transforms
from torchvision.io import read_image, ImageReadMode

def get_raw_tags(model, image_path):
    print('Start get_raw_tags')

    input_image = read_image(image_path, mode=ImageReadMode.RGB)

    preprocess = transforms.Compose([
        transforms.Resize(360),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.7137, 0.6628, 0.6519], std=[0.2970, 0.3017, 0.2979]),
    ])

    print('Preprocess initialized')

    input_tensor = preprocess(input_image)

    print('Image preprocessed')

    input_batch = input_tensor.unsqueeze(0)

    print('Image unsqueezed')

    with torch.no_grad():
        output = model(input_batch)

    print('Output got')

    probs = torch.sigmoid(output[0])

    print('Probs got')

    return probs