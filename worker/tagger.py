import torch
from torchvision import transforms

def get_raw_tags(model, input_image):
    print('Start get_raw_tags')

    preprocess = transforms.Compose([
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