import torch.nn as nn
import torch
from torchvision import models
from firebase_admin import storage
from io import BytesIO

# From https://github.com/RF5/danbooru-pretrained/blob/master/danbooru_resnet.py
# Copyright (c) 2020 M Baas licensed under MIT

class AdaptiveConcatPool2d(nn.Module):
    """
    Layer that concats `AdaptiveAvgPool2d` and `AdaptiveMaxPool2d`.
    Source: Fastai. This code was taken from the fastai library at url
    https://github.com/fastai/fastai/blob/master/fastai/layers.py#L176
    """
    def __init__(self, sz=None):
        "Output will be 2*sz or 2 if sz is None"
        super().__init__()
        self.output_size = sz or 1
        self.ap = nn.AdaptiveAvgPool2d(self.output_size)
        self.mp = nn.AdaptiveMaxPool2d(self.output_size)

    def forward(self, x): return torch.cat([self.mp(x), self.ap(x)], 1)
    
class Flatten(nn.Module):
    """
    Flatten `x` to a single dimension. Adapted from fastai's Flatten() layer,
    at https://github.com/fastai/fastai/blob/master/fastai/layers.py#L25
    """
    def __init__(self): super().__init__()
    def forward(self, x): return x.view(x.size(0), -1)

def bn_drop_lin(n_in:int, n_out:int, bn:bool=True, p:float=0., actn=None):
    """
    Sequence of batchnorm (if `bn`), dropout (with `p`) and linear (`n_in`,`n_out`) layers followed by `actn`.
    Adapted from Fastai at https://github.com/fastai/fastai/blob/master/fastai/layers.py#L44
    """
    layers = [nn.BatchNorm1d(n_in)] if bn else []
    if p != 0: layers.append(nn.Dropout(p))
    layers.append(nn.Linear(n_in, n_out))
    if actn is not None: layers.append(actn)
    return layers

def create_head(top_n_tags, nf, ps=0.5):
    nc = top_n_tags
    
    lin_ftrs = [nf, 512, nc]
    p1 = 0.25 # dropout for second last layer
    p2 = 0.5 # dropout for last layer

    actns = [nn.ReLU(inplace=True),] + [None]
    pool = AdaptiveConcatPool2d()
    layers = [pool, Flatten()]
    
    layers += [
        *bn_drop_lin(lin_ftrs[0], lin_ftrs[1], True, p1, nn.ReLU(inplace=True)),
        *bn_drop_lin(lin_ftrs[1], lin_ftrs[2], True, p2)
    ]
    
    return nn.Sequential(*layers)

def _resnet(base_arch, top_n, **kwargs):
    cut = -2
    s = base_arch(pretrained=False, **kwargs)
    body = nn.Sequential(*list(s.children())[:cut])

    if base_arch in [models.resnet18, models.resnet34]:
        num_features_model = 512
    elif base_arch in [models.resnet50, models.resnet101]:
        num_features_model = 2048

    nf = num_features_model * 2
    nc = top_n

    head = create_head(nc, nf)
    model = nn.Sequential(body, head)

    return model

def pretrained_resnet50_model():
    model = _resnet(models.resnet50, 6000)

    bucket = storage.bucket()
    model_file = bucket.blob('deepdanbooru/0.1/resnet50-13306192.pth')
    model_bytes_io = BytesIO()
    model_file.download_to_file(model_bytes_io)
    model_bytes_io.seek(0)
    model.load_state_dict(torch.load(model_bytes_io))

    return model