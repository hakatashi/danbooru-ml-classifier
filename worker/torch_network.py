import torch.nn as nn
import torch.nn.functional as F

class Network(nn.Module):
    def __init__(self):
        super(Network, self).__init__()
        self.middle1_layer = nn.Linear(6000, 512)
        self.middle2_layer = nn.Linear(512, 128)
        self.middle3_layer = nn.Linear(128, 128)
        self.out_layer = nn.Linear(128, 3)

    def forward(self, x):
        x = F.relu(self.middle1_layer(x))
        x = F.relu(self.middle2_layer(x))
        x = F.relu(self.middle3_layer(x))
        x = self.out_layer(x)
        return x


def get_torch_network():
    return Network()