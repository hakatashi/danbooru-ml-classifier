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

preference_model = None
deepdanbooru_model = None

def get_preference_model():
    bucket = storage.bucket('danbooru-ml-classifier')
    model_file = bucket.blob('preference/sklearn-multiclass-linear-svc.joblib')
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
def inferImagePreference(req: https_fn.Request) -> https_fn.Response:
    global preference_model, deepdanbooru_model

    if deepdanbooru_model is None:
        deepdanbooru_model = get_deepdanbooru_model()
        print('DeepDanbooru model loaded')
        print(deepdanbooru_model)
    else:
        print('DeepDanbooru model already loaded')

    if preference_model is None:
        preference_model = get_preference_model()
        print('Preference model loaded')
        print(preference_model)
    else:
        print('Preference model already loaded')

    bucket = storage.bucket('danbooru-ml-classifier-images')
    image_file = bucket.blob('danbooru/5744585.png')
    image_bytes_io = BytesIO()
    image_file.download_to_file(image_bytes_io)
    image_bytes_io.seek(0)

    image = Image.open(image_bytes_io)
    tags = get_raw_tags(deepdanbooru_model, image)

    inferred_class = preference_model.decision_function(tags.numpy().reshape(1, -1))

    return https_fn.Response(json.dumps(inferred_class.tolist()), mimetype='application/json')