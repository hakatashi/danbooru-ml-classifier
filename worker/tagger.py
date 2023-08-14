import torch
from torchvision import transforms

def get_raw_tags(model, input_image):
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