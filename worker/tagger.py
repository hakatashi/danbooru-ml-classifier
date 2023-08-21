import torch
from torchvision import transforms

def get_raw_tags(model, input_image):
    print('Start get_raw_tags')

    preprocess = transforms.Compose([
        transforms.Resize(360),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.7137, 0.6628, 0.6519], std=[0.2970, 0.3017, 0.2979]),
    ])

    print('Preprocess initialized')

    try:
        if input_image.mode != 'RGB':
            input_image = input_image.convert('RGB')
    except Exception as e:
        print('Image conversion failed')
        print(e)

    print('Image converted to RGB')

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