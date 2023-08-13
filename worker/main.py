from firebase_functions import https_fn, options
from firebase_admin import initialize_app, storage
from io import BytesIO
from joblib import load
import torch
from torchvision import models
from danbooru_resnet import _resnet
from tagger import get_raw_tags
from PIL import Image
import json

initialize_app()

classifier_model = None
deepdanbooru_model = None

def get_classifier_model():
    bucket = storage.bucket('danbooru-ml-classifier')
    model_file = bucket.blob('classifier/sklearn-multiclass-linear-svc.joblib')
    model_bytes_io = BytesIO()
    model_file.download_to_file(model_bytes_io)
    model_bytes_io.seek(0)

    model = load(model_bytes_io)

    return model

def get_deepdanbooru_model():
    bucket = storage.bucket('danbooru-ml-classifier')
    model_file = bucket.blob('deepdanbooru/0.1/resnet50-13306192.pth')
    model_bytes_io = BytesIO()
    model_file.download_to_file(model_bytes_io)
    model_bytes_io.seek(0)

    model = _resnet(models.resnet50, 6000)
    model.load_state_dict(torch.load(model_bytes_io, map_location=torch.device('cpu')))

    return model

@https_fn.on_request(memory=options.MemoryOption.GB_1, timeout_sec=600)
def executeTestFunction4(req: https_fn.Request) -> https_fn.Response:
    global classifier_model, deepdanbooru_model

    if deepdanbooru_model is None:
        deepdanbooru_model = get_deepdanbooru_model()
        print('DeepDanbooru model loaded')
        print(deepdanbooru_model)
    else:
        print('DeepDanbooru model already loaded')

    if classifier_model is None:
        classifier_model = get_classifier_model()
        print('Classifier model loaded')
        print(classifier_model)
    else:
        print('Classifier model already loaded')

    bucket = storage.bucket('danbooru-ml-classifier-images')
    image_file = bucket.blob('danbooru/5744585.png')
    image_bytes_io = BytesIO()
    image_file.download_to_file(image_bytes_io)
    image_bytes_io.seek(0)

    image = Image.open(image_bytes_io)
    try:
        if image.mode != 'RGB':
            image = image.convert('RGB')
    except Exception as e:
        print('Image conversion failed')
        print(e)
    tags = get_raw_tags(deepdanbooru_model, image)

    return https_fn.Response(json.dumps(tags.tolist()), mimetype='application/json')