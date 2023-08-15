from firebase_functions import firestore_fn, options
from firebase_admin import initialize_app, storage, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from io import BytesIO
from joblib import load
import torch
from torchvision import models
from danbooru_resnet import _resnet
from tagger import get_raw_tags
from PIL import Image
from urllib.parse import unquote
from torch_network import get_torch_network
import json
from urllib.request import urlopen
from firebase_functions.options import RetryConfig

initialize_app()

preference_linear_svc_model = None
preference_ada_boost_model = None
preference_torch_network_model = None
deepdanbooru_model = None
class_names = None

def get_preference_linear_svc_model():
    bucket = storage.bucket('danbooru-ml-classifier')
    model_file = bucket.blob('preference/sklearn-multiclass-linear-svc.joblib')
    model_bytes_io = BytesIO()
    model_file.download_to_file(model_bytes_io)
    model_bytes_io.seek(0)

    model = load(model_bytes_io)

    return model

def get_preference_ada_boost_model():
    bucket = storage.bucket('danbooru-ml-classifier')
    model_file = bucket.blob('preference/sklearn-multiclass-ada-boost.joblib')
    model_bytes_io = BytesIO()
    model_file.download_to_file(model_bytes_io)
    model_bytes_io.seek(0)

    model = load(model_bytes_io)

    return model

def get_preference_torch_network_model():
    bucket = storage.bucket('danbooru-ml-classifier')
    model_file = bucket.blob('preference/torch-multiclass-onehot-shallow-network-multilayer')
    model_bytes_io = BytesIO()
    model_file.download_to_file(model_bytes_io)
    model_bytes_io.seek(0)

    model = get_torch_network()
    model.load_state_dict(torch.load(model_bytes_io, map_location=torch.device('cpu')))

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

def get_top_tag_probs(tag_probs: torch.Tensor, threshold = 0.05):
    global class_names

    if class_names is None:
        print('Loading class names...')
        with urlopen("https://github.com/RF5/danbooru-pretrained/raw/master/config/class_names_6000.json") as url:
            class_names = json.loads(url.read().decode())
        print('Done loading class names')
    else:
        print('Class names already loaded')

    tmp = tag_probs[tag_probs > threshold]
    inds = tag_probs.argsort(descending=True)
    tag_dict = {}
    for i in inds[0:len(tmp)]:
        tag_dict[class_names[i]] = tag_probs[i].numpy()[()].item()

    return tag_dict

def inference_list_to_dict(inference_list: list):
    return dict(zip(
        ['not_bookmarked', 'bookmarked_public', 'bookmarked_private'],
        inference_list
    ))

def infer_image_preference(image_id: str):
    global preference_linear_svc_model, preference_ada_boost_model, preference_torch_network_model, deepdanbooru_model

    if deepdanbooru_model is None:
        deepdanbooru_model = get_deepdanbooru_model()
        print('DeepDanbooru model loaded')
        print(deepdanbooru_model)
    else:
        print('DeepDanbooru model already loaded')

    if preference_linear_svc_model is None:
        preference_linear_svc_model = get_preference_linear_svc_model()
        print('Preference LinearSVC model loaded')
        print(preference_linear_svc_model)
    else:
        print('Preference LinearSVC model already loaded')

    if preference_ada_boost_model is None:
        preference_ada_boost_model = get_preference_ada_boost_model()
        print('Preference AdaBoost model loaded')
        print(preference_ada_boost_model)
    else:
        print('Preference AdaBoost model already loaded')

    if preference_torch_network_model is None:
        preference_torch_network_model = get_preference_torch_network_model()
        print('Preference Torch Network model loaded')
        print(preference_torch_network_model)
    else:
        print('Preference Torch Network model already loaded')

    bucket = storage.bucket('danbooru-ml-classifier-images')
    image_file = bucket.blob(image_id)
    image_bytes_io = BytesIO()
    image_file.download_to_file(image_bytes_io)
    image_bytes_io.seek(0)

    image = Image.open(image_bytes_io)
    tags = get_raw_tags(deepdanbooru_model, image)
    top_tag_probs = get_top_tag_probs(tags)

    linear_svc_preference = preference_linear_svc_model.decision_function(tags.numpy().reshape(1, -1))
    ada_boost_preference = preference_ada_boost_model.decision_function(tags.numpy().reshape(1, -1))
    torch_network_preference = preference_torch_network_model(tags)

    return {
        'top_tag_probs': top_tag_probs,
        'inferences': {
            'sklearn_multiclass_linear_svc': inference_list_to_dict(linear_svc_preference.tolist()[0]),
            'sklearn_multiclass_ada_boost': inference_list_to_dict(ada_boost_preference.tolist()[0]),
            'torch_multiclass_onehot_shallow_network_multilayer': inference_list_to_dict(torch_network_preference.tolist()),
        },
    }

@firestore.transactional
def update_status_processing(transaction: firestore.Transaction):
    db = firestore.client()
    pending_images_iter = db.collection('images').where(filter=FieldFilter('status', '==', 'pending')).stream(transaction=transaction)
    pending_images = list(pending_images_iter)
    for image in pending_images:
        transaction.update(image.reference, {
            'status': 'processing',
        })
    return pending_images

@firestore_fn.on_document_created(
    memory=options.MemoryOption.GB_2,
    cpu=1,
    timeout_sec=540,
    document='images/{image_id}',
)
def onImageCreated(event: firestore_fn.Event[firestore_fn.DocumentSnapshot]):
    db = firestore.client()

    pending_count_result = db.collection('images').where(filter=FieldFilter('status', '==', 'pending')).count().get()
    pending_count = pending_count_result[0][0].value
    print(f'Pending count: {pending_count}')
    if pending_count < 100:
        return

    transaction = db.transaction()
    pending_images = update_status_processing(transaction)
    print(f'Got pending images: {len(pending_images)}')

    for image in pending_images:
        image_data = image.to_dict()
        image_id = image_data['key']
        print(f'Image created: {image_id}')

        inferences = infer_image_preference(image_id)
        print(f'Inferences: {inferences}')

        image.reference.update({
            'status': 'inferred',
            'topTagProbs': inferences['top_tag_probs'],
            'inferences': inferences['inferences'],
        })