"""
Training procedure for supervised models
"""

import os
import xarray as xr
import tensorflow as tf
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.optimizers.schedules import PiecewiseConstantDecay
from tensorflow.keras.callbacks import EarlyStopping
import horovod.tensorflow.keras as hvd
import logging
tf.get_logger().setLevel(logging.ERROR)

from .. import POSTUPSAMPLING_METHODS, SPATIAL_MODELS, SPATIOTEMP_MODELS
from ..utils import Timing
from ..dataloader import DataGenerator
from ..models import (net_pin, recnet_pin, net_postupsampling, 
                     recnet_postupsampling)
from .base import Trainer


class SupervisedTrainer(Trainer):
    """
    """
    def __init__(
        self,
        model_name, 
        data_train, 
        data_val, 
        data_test,  
        data_train_lr=None,
        data_val_lr=None,
        data_test_lr=None,
        predictors_train=None,
        predictors_val=None,
        predictors_test=None,
        loss='mae',
        batch_size=64, 
        device='GPU', 
        gpu_memory_growth=True,
        use_multiprocessing=True, 
        model_list=None,
        static_vars=None, 
        use_season=True,
        scale=5, 
        interpolation='inter_area', 
        patch_size=None, 
        time_window=None,
        epochs=60, 
        steps_per_epoch=None, 
        validation_steps=None, 
        test_steps=None,
        learning_rate=1e-4, 
        lr_decay_after=1e5,
        early_stopping=False, 
        patience=6, 
        min_delta=0, 
        show_plot=True, 
        save=False,
        save_path=None, 
        savecheckpoint_path=None,
        trained_model=None,
        trained_epochs=0,
        verbose=True,
        **architecture_params
        ):
        """Training procedure for supervised models.

        Parameters
        ----------
        model : str
            String with the name of the model architecture, either 'resnet_spc', 
            'resnet_bi' or 'resnet_rc'.
        data_train : 4D ndarray or xr.DataArray
            Training dataset with dims [nsamples, lat, lon, 1]. These grids must 
            correspond to the observational reference at HR, from which a 
            coarsened version will be created to produce paired samples. 
        data_val : 4D ndarray or xr.DataArray
            Validation dataset with dims [nsamples, lat, lon, 1]. This holdout 
            dataset is used at the end of each epoch to check the losses and 
            diagnose overfitting.
        data_test : 4D ndarray or xr.DataArray
            Testing dataset with dims [nsamples, lat, lon, 1]. Holdout not used
            during training, but only to compute metrics with the final model.
        predictors_train : list of ndarray, optional
            Predictor variables for trianing. Given as list of 4D ndarrays with 
            dims [nsamples, lat, lon, 1] or 5D ndarrays with dims 
            [nsamples, time, lat, lon, 1]. 
        predictors_val : list of ndarray, optional
            Predictor variables for validation. Given as list of 4D ndarrays
            with dims [nsamples, lat, lon, 1] or 5D ndarrays with dims 
            [nsamples, time, lat, lon, 1]. 
        predictors_test : list of ndarrays, optional
            Predictor variables for testing. Given as list of 4D ndarrays with 
            dims [nsamples, lat, lon, 1] or 5D ndarrays with dims 
            [nsamples, time, lat, lon, 1]. 
        static_vars : None or list of 2D ndarrays, optional
            Static variables such as elevation data or a binary land-ocean mask.
        scale : int, optional
            Scaling factor. 
        interpolation : str, optional
            Interpolation used when upsampling/downsampling the training samples.
        patch_size : int or None, optional
            Size of the square patches used to grab training samples.
        time_window : int or None, optional
            If not None, then each sample will have a temporal dimension 
            (``time_window`` slices to the past are grabbed for the LR array).
        batch_size : int, optional
            Batch size per replica.
        epochs : int, optional
            Number of epochs or passes through the whole training dataset. 
        steps_per_epoch : int or None, optional
            Total number of steps (batches of samples) before declaring one epoch
            finished.``batch_size * steps_per_epoch`` samples are passed per 
            epoch. If None, ``then steps_per_epoch`` is equal to the number of 
            samples diviced by the ``batch_size``.
        validation_steps : int, optional
            Steps using at the end of each epoch for drawing validation samples. 
        test_steps : int, optional
            Steps using after training for drawing testing samples.
        learning_rate : float or tuple of floats, optional
            Learning rate. If a tuple is given, it corresponds to the min and max
            LR used for a PiecewiseConstantDecay scheduler.
        lr_decay_after : float or None, optional
            Used for the PiecewiseConstantDecay scheduler.
        early_stopping : bool, optional
            Whether to use early stopping.
        patience : int, optional
            Patience for early stopping. 
        min_delta : float, otional 
            Min delta for early stopping.
        save : bool, optional
            Whether to save the final model. 
        save_path : None or str
            Path for saving the final model, running time and test score. If 
            None, then ``'./'`` is used. The SavedModel format is a 
            directory containing a protobuf binary and a TensorFlow checkpoint.
        savecheckpoint_path : None or str
            Path for saving the training checkpoints. If None, then no 
            checkpoints are saved during training.
        device : str
            Choice of 'GPU' or 'CPU' for the training of the Tensorflow models. 
        gpu_memory_growth : bool, optional
            By default, TensorFlow maps nearly all of the GPU memory of all GPUs.
            If True, we request to only grow the memory usage as is needed by 
            the process.
        show_plot : bool, optional
            If True the static plot is shown after training. 
        save_plot : bool, optional
            If True the static plot is saved to disk after training. 
        verbose : bool, optional
            Verbosity mode. False or 0 = silent. True or 1, max amount of 
            information is printed out. When equal 2, then less info is shown.
        **architecture_params : dict
            Dictionary with additional parameters passed to the neural network 
            model.
        """
        super().__init__(
            model_name=model_name, 
            data_train=data_train,
            data_train_lr=data_train_lr,
            use_season=use_season,
            loss=loss,
            batch_size=batch_size, 
            patch_size=patch_size,
            scale=scale,
            device=device, 
            gpu_memory_growth=gpu_memory_growth,
            use_multiprocessing=use_multiprocessing,
            verbose=verbose, 
            model_list=model_list,
            save=save,
            save_path=save_path,
            savecheckpoint_path=savecheckpoint_path,
            show_plot=show_plot
            )
        self.data_val = data_val
        self.data_test = data_test
        self.data_val_lr = data_val_lr
        self.data_test_lr = data_test_lr
        self.predictors_train = predictors_train
        if self.predictors_train is not None and not isinstance(self.predictors_train, list):
            raise TypeError('`predictors_train` must be a list of ndarrays')
        self.predictors_test = predictors_test
        if self.predictors_test is not None and not isinstance(self.predictors_test, list):
            raise TypeError('`predictors_test` must be a list of ndarrays')
        self.predictors_val = predictors_val
        if self.predictors_val is not None and not isinstance(self.predictors_val, list):
            raise TypeError('`predictors_val` must be a list of ndarrays')
        self.static_vars = static_vars 
        if self.static_vars is not None:
            for i in range(len(self.static_vars)):
                if isinstance(self.static_vars[i], xr.DataArray):
                    self.static_vars[i] = self.static_vars[i].values
        self.interpolation = interpolation 
        self.epochs = epochs
        self.steps_per_epoch = steps_per_epoch
        self.validation_steps = validation_steps
        self.test_steps = test_steps
        self.learning_rate = learning_rate
        self.lr_decay_after = lr_decay_after
        self.early_stopping = early_stopping
        self.patience = patience
        self.min_delta = min_delta
        self.show_plot = show_plot
        self.architecture_params = architecture_params
        self.time_window = time_window
        if self.time_window is not None and not self.model_is_spatiotemp:
            self.time_window = None
        if self.model_is_spatiotemp and self.time_window is None:
            msg = f'``model={self.model_name}``, the argument ``time_window`` must be a postive integer'
            raise ValueError(msg)
        self.trained_model = trained_model
        self.trained_epochs = trained_epochs

    def setup_datagen(self):
        """Setting up the data generators
        """
        datagen_params = dict(
            scale=self.scale, 
            batch_size=self.global_batch_size,
            static_vars=self.static_vars, 
            patch_size=self.patch_size, 
            model=self.model_name, 
            interpolation=self.interpolation,
            time_window=self.time_window,
            use_season=self.use_season)
        self.ds_train = DataGenerator(
            self.data_train, self.data_train_lr, 
            predictors=self.predictors_train, **datagen_params)
        self.ds_val = DataGenerator(
            self.data_val, self.data_val_lr, 
            predictors=self.predictors_val, **datagen_params)
        self.ds_test = DataGenerator(
            self.data_test, self.data_test_lr,
            predictors=self.predictors_test, **datagen_params)

    def setup_model(self):
        """Setting up the model
        """
        ### number of channels
        if self.model_name in SPATIAL_MODELS:
            n_channels = self.data_train.shape[-1]
            n_aux_channels = 0
            if self.static_vars is not None:
                n_channels += len(self.static_vars)
                n_aux_channels = len(self.static_vars)
            if self.use_season:
                n_channels += 4
                n_aux_channels += 4
            if self.predictors_train is not None:
                n_channels += len(self.predictors_train)
        elif self.model_name in SPATIOTEMP_MODELS:
            n_channels = self.data_train.shape[-1]
            n_aux_channels = 0
            if self.predictors_train is not None:
                n_channels += len(self.predictors_train)
            if self.static_vars is not None:
                n_aux_channels += len(self.static_vars)
            if self.use_season:
                n_aux_channels += 4

        if self.patch_size is None:
            lr_height = int(self.data_train.shape[1] / self.scale)
            lr_width = int(self.data_train.shape[2] / self.scale)
            hr_height = int(self.data_train.shape[1])
            hr_width = int(self.data_train.shape[2])
        else:
            lr_height = lr_width = int(self.patch_size / self.scale)
            hr_height = hr_width = int(self.patch_size)

        ### instantiating the model
        if self.trained_model is None:
            if self.upsampling in POSTUPSAMPLING_METHODS:
                if not self.model_is_spatiotemp:
                    self.model = net_postupsampling(
                        backbone_block=self.backbone,
                        upsampling=self.upsampling, 
                        scale=self.scale, 
                        lr_size=(lr_height, lr_width),
                        n_channels=n_channels, 
                        n_aux_channels=n_aux_channels,
                        **self.architecture_params)
                else:
                    self.model = recnet_postupsampling(
                        backbone_block=self.backbone,
                        upsampling=self.upsampling, 
                        scale=self.scale, 
                        n_channels=n_channels, 
                        n_aux_channels=n_aux_channels,
                        lr_size=(lr_height, lr_width),
                        time_window=self.time_window, 
                        **self.architecture_params)
            elif self.upsampling == 'pin':
                if not self.model_is_spatiotemp:
                    self.model = net_pin(
                        backbone_block=self.backbone,
                        n_channels=n_channels, 
                        n_aux_channels=n_aux_channels,
                        hr_size=(hr_height, hr_width),
                        **self.architecture_params)        
                else:
                    self.model = recnet_pin(
                        backbone_block=self.backbone,
                        n_channels=n_channels,
                        n_aux_channels=n_aux_channels,
                        hr_size=(hr_height, hr_width),
                        time_window=self.time_window, 
                        **self.architecture_params)

            if self.verbose == 1 and self.running_on_first_worker:
                self.model.summary(line_length=150)
        
        # loading pre-trained model
        else:
            self.model = self.trained_model
            print('Loading pre-trained model')


    def run(self):
        """Compiling, training and saving the model
        """
        self.timing = Timing(self.verbose)
        self.setup_datagen()
        self.setup_model()

        ### Setting up the optimizer
        if isinstance(self.learning_rate, tuple):
            ### Adam optimizer with a scheduler 
            self.learning_rate = PiecewiseConstantDecay(boundaries=[self.lr_decay_after], 
                                                        values=[self.learning_rate[0], 
                                                                self.learning_rate[1]])
        elif isinstance(self.learning_rate, float):
            # as in Goyan et al 2018 (https://arxiv.org/abs/1706.02677)
            self.learning_rate *= hvd.size()
        self.optimizer = Adam(learning_rate=self.learning_rate)

        ### Callbacks
        # early stopping
        callbacks = []
        if self.early_stopping:
            earlystop = EarlyStopping(monitor='val_loss', mode='min', patience=self.patience, 
                                      min_delta=self.min_delta, verbose=self.verbose)
            callbacks.append(earlystop)

        # Horovod: add Horovod DistributedOptimizer.
        self.optimizer = hvd.DistributedOptimizer(self.optimizer)
        # Horovod: broadcast initial variable states from rank 0 to all other processes.
        # This is necessary to ensure consistent initialization of all workers when
        # training is started with random weights or restored from a checkpoint.
        callbacks.append(hvd.callbacks.BroadcastGlobalVariablesCallback(0))
        
        # verbosity for model.fit
        if self.verbose == 1 and self.running_on_first_worker:
            verbose = 1
        elif self.verbose == 2 and self.running_on_first_worker:
            verbose = 2
        else:
            verbose = 0

        # Model checkopoints are saved at the end of every epoch, if it's the best seen so far.
        if self.savecheckpoint_path is not None:
            os.makedirs(self.savecheckpoint_path, exist_ok=True)
            model_checkpoint_callback = tf.keras.callbacks.ModelCheckpoint(
                os.path.join(self.savecheckpoint_path, './best_model'), 
                save_weights_only=False,
                monitor='val_loss',
                mode='min',
                save_best_only=True)
            # Horovod: save checkpoints only on worker 0 to prevent other workers from corrupting them.
            if self.running_on_first_worker:
                callbacks.append(model_checkpoint_callback)

        ### Compiling and training the model
        if self.steps_per_epoch is not None:
            self.steps_per_epoch = self.steps_per_epoch // hvd.size()

        self.model.compile(optimizer=self.optimizer, loss=self.lossf)
        self.fithist = self.model.fit(
            self.ds_train, 
            epochs=self.epochs, 
            initial_epoch=self.trained_epochs,
            steps_per_epoch=self.steps_per_epoch,
            validation_data=self.ds_val, 
            validation_steps=self.validation_steps, 
            verbose=self.verbose if self.running_on_first_worker else False, 
            callbacks=callbacks,
            use_multiprocessing=self.use_multiprocessing)
        
        if self.running_on_first_worker:
            self.test_loss = self.model.evaluate(self.ds_test, 
                steps=self.test_steps, verbose=verbose)
            
            if self.verbose:
                print(f'\nScore on the test set: {self.test_loss}')
            
            self.timing.runtime()

        self.save_results(self.model)
