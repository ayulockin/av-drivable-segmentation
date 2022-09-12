import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
import glob
import numpy as np
import pandas as pd
from absl import app
from absl import flags
from ml_collections.config_flags import config_flags

import wandb
from wandb.keras import WandbCallback
import tensorflow as tf
from tensorflow import keras

from drivable.data import GetDrivableDataloader
from drivable.model import get_unet_model, build_deeplabv3_plus
from drivable.utils.devices import initialize_device
from drivable import callbacks

# Config
FLAGS = flags.FLAGS
CONFIG = config_flags.DEFINE_config_file("config")
flags.DEFINE_bool("wandb", False, "MLOps pipeline for our classifier.")
flags.DEFINE_bool("log_model", False, "Checkpoint model while training.")
# flags.DEFINE_bool("log_eval", False, "Log model prediction, needs --wandb argument as well.")

TRAIN_DATA_PATH = f"/home/manan_goel/av-segmentation/artifacts/bdd100k-dataset:v0/images/100k/train"
TRAIN_MASK_PATH = f"/home/manan_goel/av-segmentation/artifacts/train_masks:v0"
VAL_MASK_PATH = f"/home/manan_goel/av-segmentation/artifacts/train_masks:v0"
    
# img_paths = glob.glob(f"{TRAIN_DATA_PATH}/*.jpg")
# mask_paths = glob.glob(f"{TRAIN_MASK_PATH}/*.png")

def get_model(dir):
    model = keras.models.load_model(f"{dir}")
    x = model.layers[-2].output
    predictions = keras.layers.Conv2D(3, kernel_size=(1, 1), padding="same", name='final')(x)
    model = keras.Model(inputs=model.input, outputs=predictions)

    return model

with open("splits/train_split.txt", "r") as f:
    img_paths = f.readlines()
    train_img_paths = []
    train_mask_paths = []

    for path in img_paths:
        p = path.split(".")[0]
        train_img_paths.append(f"{TRAIN_DATA_PATH}/{p}.jpg")
        train_mask_paths.append(f"{TRAIN_MASK_PATH}/{p}.png")

with open("splits/val_split.txt", "r") as f:
    img_paths = f.readlines()
    val_img_paths = []
    val_mask_paths = []

    for path in img_paths:
        p = path.split(".")[0]
        val_img_paths.append(f"{TRAIN_DATA_PATH}/{p}.jpg")
        val_mask_paths.append(f"{VAL_MASK_PATH}/{p}.png")


def main(_):
    # Get configs from the config file.
    config = CONFIG.value
    print(config)

    # Detect strategy
    strategy = initialize_device()
    batch_size = (
        config.dataset_config.batch_size
        * strategy.num_replicas_in_sync
    )
    config.dataset_config.batch_size = batch_size

    CALLBACKS = []
    # Initialize a Weights and Biases run.
    if FLAGS.wandb:
        run = wandb.init(
            entity=CONFIG.value.wandb_config.entity,
            project=CONFIG.value.wandb_config.project,
            job_type='train',
            name="bdd100k-finetune",
            config=config.to_dict(),
        )
        # Initialize W&B metrics logger callback.
        CALLBACKS += [callbacks.WandBMetricsLogger(config.callback_config.log_batch_frequency)]

    # Download and get dataset
    # dataset_name = config.dataset_config.dataset_name
    # info, (train_images, train_labels) = download_and_get_dataset(dataset_name, 'train')
    # info, (valid_images, valid_labels) = download_and_get_dataset(dataset_name, 'valid')

    # Get dataloader
    make_dataloader = GetDrivableDataloader(config)
    trainloader = make_dataloader.get_dataloader(train_img_paths, train_mask_paths)
    validloader = make_dataloader.get_dataloader(val_img_paths, val_mask_paths, dataloader_type="valid")
    imgs, masks = next(iter(trainloader))
    print(imgs.shape, masks.shape)

    # with strategy.scope():
        # Get model
    tf.keras.backend.clear_session()
    if wandb.run is not None:
        artifact = wandb.run.use_artifact("av-team/drivable-segmentation/run_1t1qdtpr_model:latest")
    else:
        api = wandb.Api()
        artifact = api.artifact("av-team/drivable-segmentation/run_1t1qdtpr_model:latest")
    
    artifact_dir = artifact.download()
    model = get_model(artifact_dir)

        # if config.train_config.loss == "sparse_categorical_crossentropy":
        #     loss = tf.keras.losses.SparseCategoricalCrossentropy()

        # if config.train_config.optimizer == "adam":
        #     optimizer = tf.keras.optimizers.Adam(
                
        #     )

    # Initialize callbacks
    callback_config = config.callback_config
    # Builtin early stopping callback
    if callback_config.use_earlystopping:
        earlystopper = callbacks.get_earlystopper(config)
        CALLBACKS += [earlystopper]
    # Built in callback to reduce learning rate on plateau
    if callback_config.use_reduce_lr_on_plateau:
        reduce_lr_on_plateau = callbacks.get_reduce_lr_on_plateau(config)
        CALLBACKS += [reduce_lr_on_plateau]
    
    # Initialize Model checkpointing callback
    if FLAGS.log_model:
        # Custom W&B model checkpoint callback
        model_checkpointer = callbacks.get_model_checkpoint_callback(config)
        CALLBACKS += [model_checkpointer]    

    # # Custom W&B model prediction visualization callback
    # if wandb.run is not None:
    #     if FLAGS.log_eval:
    #         model_pred_viz = get_evaluation_callback(config, validloader)
    #         CALLBACKS += [model_pred_viz]

    # Compile the model
    model.compile(
        optimizer = config.train_config.optimizer,
        loss = config.train_config.loss,
        metrics = config.train_config.metrics
    )

    # Train the model
    model.fit(
        trainloader,
        validation_data = validloader,
        epochs = config.train_config.epochs,
        callbacks=CALLBACKS
    )


if __name__ == "__main__":
    app.run(main)