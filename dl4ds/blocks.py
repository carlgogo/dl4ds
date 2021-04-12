from tensorflow.keras.layers import Add, Conv2D, Lambda
from .attention import ChannelAttention2D

def residual_block(x_in, filters, scaling=None, attention=False):
    """Creates an EDSR residual block."""
    x = Conv2D(filters, (3, 3), padding='same', activation='relu')(x_in)
    x = Conv2D(filters, (3, 3), padding='same')(x)
    if scaling is not None:
        x = Lambda(lambda t: t * scaling)(x)
    if attention:
        x = ChannelAttention2D(x.shape[-1])(x)
    x = Add()([x_in, x])
    return x


def normalize(params):
    x, x_train_mean, x_train_std = params
    return (x - x_train_mean) / x_train_std


def denormalize(params):
    x, x_train_mean, x_train_std = params
    return x * x_train_std + x_train_mean