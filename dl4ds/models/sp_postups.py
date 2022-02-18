import tensorflow as tf
from tensorflow.keras.layers import (Add, Conv2D, Input, UpSampling2D, 
                                     Concatenate)
from tensorflow.keras.models import Model

from .blocks import (ResidualBlock, ConvBlock, DeconvolutionBlock,
                     DenseBlock, TransitionBlock, SubpixelConvolutionBlock,
                     LocalizedConvBlock, choose_dropout_layer)
from ..utils import (checkarg_backbone, checkarg_upsampling, 
                    checkarg_dropout_variant)


def net_postupsampling(
    backbone_block,
    upsampling,
    scale, 
    n_channels, 
    n_aux_channels,
    n_filters, 
    n_blocks, 
    lr_size,
    n_channels_out=1, 
    normalization=None,
    dropout_rate=0,
    dropout_variant=None,
    attention=False,
    activation='relu',
    output_activation=None,
    localcon_layer=False):
    """
    Deep neural network with different backbone architectures (according to the
    ``backbone_block``) and post-upsampling methods (according to 
    ``upsampling``).

    Parameters
    ----------
    normalization : str or None, optional
        Normalization method in the residual or dense block. Can be either 'bn'
        for BatchNormalization or 'ln' for LayerNormalization. If None, then no
        normalization is performed. For the 'resnet' backbone, it results in the
        EDSR-style residual block.
    dropout_rate : float, optional
        Float between 0 and 1. Fraction of the input units to drop. If 0 then no
        dropout is applied. 
    dropout_variant : str or None, optional
        Type of dropout: gaussian, block, spatial. 
    """
    backbone_block = checkarg_backbone(backbone_block)
    upsampling = checkarg_upsampling(upsampling)
    dropout_variant = checkarg_dropout_variant(dropout_variant)

    h_lr = lr_size[0]
    w_lr = lr_size[1]
    if upsampling is not None:
        h_hr = int(h_lr * scale)
        w_hr = int(w_lr * scale)                                    

    auxvar_array_is_given = True if n_aux_channels > 0 else False
    if auxvar_array_is_given:
        if not localcon_layer:
            s_in = Input(shape=(None, None, n_aux_channels))
        else:
            s_in = Input(shape=(h_hr, w_hr, n_aux_channels))

    if not localcon_layer:
        x_in = Input(shape=(None, None, n_channels))
    else:
        x_in = Input(shape=(h_lr, w_lr, n_channels))
    x = b = Conv2D(n_filters, (3, 3), padding='same')(x_in)
    
    #---------------------------------------------------------------------------
    # N conv blocks
    for i in range(n_blocks):
        if backbone_block == 'convnet':
            b = ConvBlock(
                n_filters, activation=activation, dropout_rate=dropout_rate, 
                dropout_variant=dropout_variant, normalization=normalization, 
                attention=attention)(b)
        elif backbone_block == 'resnet':
            b = ResidualBlock(
                n_filters, activation=activation, dropout_rate=dropout_rate, 
                dropout_variant=dropout_variant, normalization=normalization, 
                attention=attention)(b)
        elif backbone_block == 'densenet':
            b = DenseBlock(
                n_filters, activation=activation, dropout_rate=dropout_rate, 
                dropout_variant=dropout_variant, normalization=normalization, 
                attention=attention)(b)
            b = TransitionBlock(n_filters // 2)(b)  # another option: half of the DenseBlock channels
    b = Conv2D(n_filters, (3, 3), padding='same', activation=activation)(b)
    
    b = choose_dropout_layer(b, dropout_rate, dropout_variant)

    if backbone_block == 'convnet':
        x = b
    elif backbone_block == 'resnet':
        x = Add()([x, b])
    elif backbone_block == 'densenet':
        x = Concatenate()([x, b])
    
    #---------------------------------------------------------------------------
    # Upsampling
    model_name = backbone_block + '_' + upsampling
    if upsampling == 'spc':
        x = SubpixelConvolutionBlock(scale, n_filters)(x)
        x = Conv2D(n_filters, (3, 3), padding='same', activation=activation)(x)
    elif upsampling == 'rc':
        x = UpSampling2D(scale, interpolation='bilinear')(x)
        x = Conv2D(n_filters, (3, 3), padding='same', activation=activation)(x)
    elif upsampling == 'dc':
        x = DeconvolutionBlock(scale, n_filters, activation)(x)
    
    #---------------------------------------------------------------------------
     # Localized convolutional layer
    if localcon_layer:
        lws = LocalizedConvBlock(filters=2, use_bias=True)(x)
        x = Concatenate()([x, lws])
    
    #---------------------------------------------------------------------------
    # HR aux channels are processed
    if auxvar_array_is_given:
        s = ConvBlock(n_filters, activation=activation, dropout_rate=0, 
            normalization=normalization, attention=False)(s_in) 
        x = Concatenate()([x, s])   
    
    #---------------------------------------------------------------------------
    # Last conv layers
    x = ConvBlock(n_filters, activation=None, dropout_rate=dropout_rate, 
        normalization=normalization, attention=True)(x)  

    x = ConvBlock(n_channels_out, activation=output_activation, dropout_rate=0, 
        normalization=normalization, attention=False)(x) 

    if auxvar_array_is_given:
        return Model(inputs=[x_in, s_in], outputs=x, name=model_name)  
    else:
        return Model(inputs=[x_in], outputs=x, name=model_name)  
