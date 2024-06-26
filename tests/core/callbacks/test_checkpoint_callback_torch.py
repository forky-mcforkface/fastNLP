import os
import pytest
from typing import Any
from dataclasses import dataclass
from pathlib import Path
import re
import time

from fastNLP.core.callbacks.checkpoint_callback import CheckpointCallback
from fastNLP.core.controllers.trainer import Trainer
from fastNLP import Evaluator
from fastNLP.envs import FASTNLP_LAUNCH_TIME, FASTNLP_DISTRIBUTED_CHECK

from tests.helpers.utils import magic_argv_env_context
from fastNLP.envs.distributed import rank_zero_rm
from tests.helpers.models.torch_model import TorchNormalModel_Classification_1
from tests.helpers.datasets.torch_data import TorchArgMaxDataset
from tests.helpers.utils import Capturing
from fastNLP.core.log import logger

from fastNLP.envs.imports import _NEED_IMPORT_TORCH
if _NEED_IMPORT_TORCH:
    from torch.utils.data import DataLoader
    from torch.optim import SGD
    import torch.distributed as dist
    from torchmetrics import Accuracy

@dataclass
class ArgMaxDatasetConfig:
    num_labels: int = 10
    feature_dimension: int = 10
    data_num: int = 50
    seed: int = 0

    batch_size: int = 2
    shuffle: bool = True



@dataclass
class TrainerParameters:
    model: Any = None
    optimizers: Any = None
    train_dataloader: Any = None
    evaluate_dataloaders: Any = None
    input_mapping: Any = None
    output_mapping: Any = None
    metrics: Any = None


@pytest.fixture(scope="module", params=[0], autouse=True)
def model_and_optimizers(request):
    trainer_params = TrainerParameters()

    trainer_params.model = TorchNormalModel_Classification_1(
        num_labels=ArgMaxDatasetConfig.num_labels,
        feature_dimension=ArgMaxDatasetConfig.feature_dimension
    )
    trainer_params.optimizers = SGD(trainer_params.model.parameters(), lr=0.001)
    dataset = TorchArgMaxDataset(
        feature_dimension=ArgMaxDatasetConfig.feature_dimension,
        data_num=ArgMaxDatasetConfig.data_num,
        seed=ArgMaxDatasetConfig.seed
    )
    _dataloader = DataLoader(
        dataset=dataset,
        batch_size=ArgMaxDatasetConfig.batch_size,
        shuffle=True
    )
    trainer_params.train_dataloader = _dataloader
    trainer_params.evaluate_dataloaders = _dataloader
    trainer_params.metrics = {"acc": Accuracy()}

    return trainer_params


@pytest.mark.torch
@pytest.mark.parametrize("driver,device", [("torch", [4, 5])])  # ("torch", "cpu"), ("torch", [0, 1]), ("torch", 1)
@magic_argv_env_context(timeout=100)
def test_model_checkpoint_callback_1(
    model_and_optimizers: TrainerParameters,
    driver,
    device
):
    for version in [0, 1]:
        for only_state_dict in [True, False]:
            try:
                path = Path.cwd().joinpath(f"test_model_checkpoint")
                path.mkdir(exist_ok=True, parents=True)

                if version == 0:
                    callbacks = [
                        CheckpointCallback(folder=path, every_n_epochs=1, every_n_batches=123, last=False, on_exceptions=None, topk=0,
                                           monitor=None, only_state_dict=only_state_dict, save_object='model')
                    ]
                elif version == 1:
                    callbacks = [
                        CheckpointCallback(folder=path, every_n_epochs=3, every_n_batches=None, last=True, on_exceptions=None, topk=2,
                                           monitor="acc", only_state_dict=only_state_dict, save_object='model')
                    ]

                trainer = Trainer(
                    model=model_and_optimizers.model,
                    driver=driver,
                    device=device,
                    optimizers=model_and_optimizers.optimizers,
                    train_dataloader=model_and_optimizers.train_dataloader,
                    evaluate_dataloaders=model_and_optimizers.evaluate_dataloaders,
                    input_mapping=model_and_optimizers.input_mapping,
                    output_mapping=model_and_optimizers.output_mapping,
                    metrics=model_and_optimizers.metrics,
                    n_epochs=10,
                    callbacks=callbacks,
                    output_from_new_proc="all"
                )

                trainer.run()
                print("Finish train")
                all_saved_model_paths = {w.name: w for w in path.joinpath(os.environ[FASTNLP_LAUNCH_TIME]).iterdir()}
                # 检查生成保存模型文件的数量是不是正确的；
                if version == 0:

                    if not isinstance(device, list):
                        assert "model-epoch_10" in all_saved_model_paths
                        assert "model-epoch_4-batch_123" in all_saved_model_paths

                        epoch_save_path = all_saved_model_paths["model-epoch_10"]
                        step_save_path = all_saved_model_paths["model-epoch_4-batch_123"]

                        assert len(all_saved_model_paths) == 12
                    # ddp 下的文件名不同，因为同样的数据，ddp 用了更少的步数跑完；
                    else:
                        assert "model-epoch_6" in all_saved_model_paths
                        assert "model-epoch_9-batch_123" in all_saved_model_paths

                        epoch_save_path = all_saved_model_paths["model-epoch_6"]
                        step_save_path = all_saved_model_paths["model-epoch_9-batch_123"]

                        assert len(all_saved_model_paths) == 11
                    all_state_dicts = [epoch_save_path, step_save_path]

                elif version == 1:

                    pattern = re.compile("model-epoch_[0-9]+-batch_[0-9]+-[a-zA-Z#]+_[0-9]*.?[0-9]*")

                    if not isinstance(device, list):
                        assert "model-epoch_9" in all_saved_model_paths
                        assert "model-last" in all_saved_model_paths
                        aLL_topk_folders = []
                        for each_folder_name in all_saved_model_paths:
                            each_folder_name = pattern.findall(each_folder_name)
                            if len(each_folder_name) != 0:
                                aLL_topk_folders.append(each_folder_name[0])
                        assert len(aLL_topk_folders) == 2

                        epoch_save_path = all_saved_model_paths["model-epoch_9"]
                        last_save_path = all_saved_model_paths["model-last"]
                        topk_save_path = all_saved_model_paths[aLL_topk_folders[0]]

                        assert len(all_saved_model_paths) == 6
                    # ddp 下的文件名不同，因为同样的数据，ddp 用了更少的步数跑完；
                    else:
                        assert "model-epoch_9" in all_saved_model_paths
                        assert "model-last" in all_saved_model_paths

                        aLL_topk_folders = []
                        for each_folder_name in all_saved_model_paths:
                            each_folder_name = pattern.findall(each_folder_name)
                            if len(each_folder_name) != 0:
                                aLL_topk_folders.append(each_folder_name[0])
                        assert len(aLL_topk_folders) == 2

                        epoch_save_path = all_saved_model_paths["model-epoch_9"]
                        last_save_path = all_saved_model_paths["model-last"]
                        topk_save_path = all_saved_model_paths[aLL_topk_folders[0]]

                        assert len(all_saved_model_paths) == 6

                    all_state_dicts = [epoch_save_path, last_save_path, topk_save_path]

                for folder in all_state_dicts:
                    trainer = Trainer(
                        model=model_and_optimizers.model,
                        driver=driver,
                        device=device,
                        optimizers=model_and_optimizers.optimizers,
                        train_dataloader=model_and_optimizers.train_dataloader,
                        evaluate_dataloaders=model_and_optimizers.evaluate_dataloaders,
                        input_mapping=model_and_optimizers.input_mapping,
                        output_mapping=model_and_optimizers.output_mapping,
                        metrics=model_and_optimizers.metrics,

                        n_epochs=2,
                        output_from_new_proc="all"
                    )
                    trainer.load_model(folder, only_state_dict=only_state_dict)

                    trainer.run()
                    trainer.driver.barrier()
            finally:
                rank_zero_rm(path)

    if dist.is_initialized():
        dist.destroy_process_group()


@pytest.mark.torch
@pytest.mark.parametrize("driver,device", [("torch", "cpu"), ("torch", [0, 1])])  # ("torch", "cpu"), ("torch", [0, 1]), ("torch", 1)
@pytest.mark.parametrize("only_state_dict", [True, False])  # ("torch", "cpu"), ("torch", [0, 1]), ("torch", 1)
@magic_argv_env_context(timeout=100)
def test_model_checkpoint_callback_2(
        model_and_optimizers: TrainerParameters,
        driver,
        device,
        only_state_dict
):
    try:
        path = Path.cwd().joinpath("test_model_checkpoint")
        path.mkdir(exist_ok=True, parents=True)

        from fastNLP.core.callbacks.callback_event import Event

        @Trainer.on(Event.on_train_epoch_end())
        def raise_exception(trainer):
            if trainer.driver.get_local_rank() == 0 and trainer.cur_epoch_idx == 4:
                raise NotImplementedError

        callbacks = [
            CheckpointCallback(folder=path, every_n_epochs=None, every_n_batches=None, last=False,
                               on_exceptions=NotImplementedError, topk=None, monitor=None, only_state_dict=only_state_dict,
                               save_object='model'),
        ]

        with pytest.raises(NotImplementedError):
            trainer = Trainer(
                model=model_and_optimizers.model,
                driver=driver,
                device=device,
                optimizers=model_and_optimizers.optimizers,
                train_dataloader=model_and_optimizers.train_dataloader,
                evaluate_dataloaders=model_and_optimizers.evaluate_dataloaders,
                input_mapping=model_and_optimizers.input_mapping,
                output_mapping=model_and_optimizers.output_mapping,
                metrics=model_and_optimizers.metrics,

                n_epochs=10,
                callbacks=callbacks,
                output_from_new_proc="all"
            )

            trainer.run()

        if dist.is_initialized():
            dist.destroy_process_group()
            if FASTNLP_DISTRIBUTED_CHECK in os.environ:
                os.environ.pop(FASTNLP_DISTRIBUTED_CHECK)

        # 检查生成保存模型文件的数量是不是正确的；
        all_saved_model_paths = {w.name: w for w in path.joinpath(os.environ[FASTNLP_LAUNCH_TIME]).iterdir()}

        if not isinstance(device, list):
            assert "model-epoch_4-batch_100-exception_NotImplementedError" in all_saved_model_paths
            exception_model_path = all_saved_model_paths["model-epoch_4-batch_100-exception_NotImplementedError"]
        # ddp 下的文件名不同，因为同样的数据，ddp 用了更少的步数跑完；
        else:
            assert "model-epoch_4-batch_52-exception_NotImplementedError" in all_saved_model_paths
            exception_model_path = all_saved_model_paths["model-epoch_4-batch_52-exception_NotImplementedError"]

        assert len(all_saved_model_paths) == 1
        all_state_dicts = [exception_model_path]

        for folder in all_state_dicts:
            trainer = Trainer(
                model=model_and_optimizers.model,
                driver="torch",
                device=0,
                optimizers=model_and_optimizers.optimizers,
                train_dataloader=model_and_optimizers.train_dataloader,
                evaluate_dataloaders=model_and_optimizers.evaluate_dataloaders,
                input_mapping=model_and_optimizers.input_mapping,
                output_mapping=model_and_optimizers.output_mapping,
                metrics=model_and_optimizers.metrics,

                n_epochs=2,
                output_from_new_proc="all"
            )

            trainer.load_model(folder, only_state_dict=only_state_dict)
            trainer.run()
            evaluator = Evaluator(model=model_and_optimizers.model, driver='torch', device=0,
                                  dataloaders=model_and_optimizers.evaluate_dataloaders,
                                  input_mapping=model_and_optimizers.input_mapping,
                                  output_mapping=model_and_optimizers.output_mapping,
                                  metrics=model_and_optimizers.metrics)
            evaluator.load_model(folder, only_state_dict=only_state_dict)
            evaluator.run()
            trainer.driver.barrier()

    finally:
        rank_zero_rm(path)
        # pass

    if dist.is_initialized():
        dist.destroy_process_group()


@pytest.mark.torch
@pytest.mark.parametrize("driver,device", [("torch", "cpu"), ("torch", [0, 1])])  # ("torch", "cpu"), ("torch", [0, 1]), ("torch", 1)
@magic_argv_env_context(timeout=100)
def test_trainer_checkpoint_callback_1(
    model_and_optimizers: TrainerParameters,
    driver,
    device
):
    for version in [0, 1]:
        for only_state_dict in [True, False]:
            try:
                path = Path.cwd().joinpath(f"test_model_checkpoint")
                path.mkdir(exist_ok=True, parents=True)

                if version == 0:
                    callbacks = [
                        CheckpointCallback(folder=path, every_n_epochs=7, every_n_batches=123, last=False, on_exceptions=None, topk=0,
                                           monitor=None, only_state_dict=only_state_dict, save_object='trainer')
                    ]
                elif version == 1:
                    callbacks = [
                        CheckpointCallback(folder=path, every_n_epochs=None, every_n_batches=None, last=True, on_exceptions=None,
                                           topk=2, monitor="acc", only_state_dict=only_state_dict, save_object='trainer')
                    ]

                trainer = Trainer(
                    model=model_and_optimizers.model,
                    driver=driver,
                    device=device,
                    optimizers=model_and_optimizers.optimizers,
                    train_dataloader=model_and_optimizers.train_dataloader,
                    evaluate_dataloaders=model_and_optimizers.evaluate_dataloaders,
                    input_mapping=model_and_optimizers.input_mapping,
                    output_mapping=model_and_optimizers.output_mapping,
                    metrics=model_and_optimizers.metrics,

                    n_epochs=10,
                    callbacks=callbacks,
                    output_from_new_proc="all"
                )

                trainer.run()

                all_saved_model_paths = {w.name: w for w in path.joinpath(os.environ[FASTNLP_LAUNCH_TIME]).iterdir()}
                # 检查生成保存模型文件的数量是不是正确的；
                if version == 0:

                    if not isinstance(device, list):
                        assert "trainer-epoch_7" in all_saved_model_paths
                        assert "trainer-epoch_4-batch_123" in all_saved_model_paths

                        epoch_save_path = all_saved_model_paths["trainer-epoch_7"]
                        step_save_path = all_saved_model_paths["trainer-epoch_4-batch_123"]

                        assert len(all_saved_model_paths) == 3
                    # ddp 下的文件名不同，因为同样的数据，ddp 用了更少的步数跑完；
                    else:
                        assert "trainer-epoch_7" in all_saved_model_paths
                        assert "trainer-epoch_9-batch_123" in all_saved_model_paths

                        epoch_save_path = all_saved_model_paths["trainer-epoch_7"]
                        step_save_path = all_saved_model_paths["trainer-epoch_9-batch_123"]

                        assert len(all_saved_model_paths) == 2
                    all_state_dicts = [epoch_save_path, step_save_path]

                elif version == 1:

                    pattern = re.compile("trainer-epoch_[0-9]+-batch_[0-9]+-[a-zA-Z#]+_[0-9]*.?[0-9]*")

                    # all_saved_model_paths = {w.name: w for w in path.joinpath(os.environ[FASTNLP_LAUNCH_TIME]).iterdir()}
                    if not isinstance(device, list):
                        assert "trainer-last" in all_saved_model_paths
                        aLL_topk_folders = []
                        for each_folder_name in all_saved_model_paths:
                            each_folder_name = pattern.findall(each_folder_name)
                            if len(each_folder_name) != 0:
                                aLL_topk_folders.append(each_folder_name[0])
                        assert len(aLL_topk_folders) == 2

                        last_save_path = all_saved_model_paths["trainer-last"]
                        topk_save_path = all_saved_model_paths[aLL_topk_folders[0]]

                        assert len(all_saved_model_paths) == 3
                    # ddp 下的文件名不同，因为同样的数据，ddp 用了更少的步数跑完；
                    else:
                        assert "trainer-last" in all_saved_model_paths

                        aLL_topk_folders = []
                        for each_folder_name in all_saved_model_paths:
                            each_folder_name = pattern.findall(each_folder_name)
                            if len(each_folder_name) != 0:
                                aLL_topk_folders.append(each_folder_name[0])
                        assert len(aLL_topk_folders) == 2

                        last_save_path = all_saved_model_paths["trainer-last"]
                        topk_save_path = all_saved_model_paths[aLL_topk_folders[0]]

                        assert len(all_saved_model_paths) == 3

                    all_state_dicts = [last_save_path, topk_save_path]

                for folder in all_state_dicts:
                    trainer = Trainer(
                        model=model_and_optimizers.model,
                        driver=driver,
                        device=device,
                        optimizers=model_and_optimizers.optimizers,
                        train_dataloader=model_and_optimizers.train_dataloader,
                        evaluate_dataloaders=model_and_optimizers.evaluate_dataloaders,
                        input_mapping=model_and_optimizers.input_mapping,
                        output_mapping=model_and_optimizers.output_mapping,
                        metrics=model_and_optimizers.metrics,

                        n_epochs=13,
                        output_from_new_proc="all"
                    )
                    trainer.load_checkpoint(folder, only_state_dict=only_state_dict)

                    trainer.run()
                    trainer.driver.barrier()

            finally:
                rank_zero_rm(path)

    if dist.is_initialized():
        dist.destroy_process_group()


@pytest.mark.torch
def test_load_state(model_and_optimizers):
    try:
        path = Path.cwd().joinpath(f"test_model_checkpoint")
        path.mkdir(exist_ok=True, parents=True)
        from fastNLP import Event, Callback
        @Trainer.on(Event.on_before_backward(every=3), marker='all')
        def print_outputs(*args):
            print("????")

        class StateCallback(Callback):
            def __init__(self, name):
                self.name = name

            def on_save_checkpoint(self, trainer):
                return {'name': self.name}

            def on_load_checkpoint(self, trainer, states):
                self.name = states['name']

            def on_train_end(self, trainer):
                print(self.name)

        callbacks = [StateCallback('old_callback1'), StateCallback('old_callback2'),
                     CheckpointCallback(folder=path, every_n_epochs=1, save_object='trainer')]

        trainer = Trainer(
            model=model_and_optimizers.model,
            driver='torch',
            device='cpu',
            optimizers=model_and_optimizers.optimizers,
            train_dataloader=model_and_optimizers.train_dataloader,
            evaluate_dataloaders=model_and_optimizers.evaluate_dataloaders,
            input_mapping=model_and_optimizers.input_mapping,
            output_mapping=model_and_optimizers.output_mapping,
            metrics=model_and_optimizers.metrics,
            n_epochs=3,
            callbacks=callbacks,
            output_from_new_proc="all"
        )
        trainer.run(num_eval_sanity_batch=0, num_train_batch_per_epoch=2)

        all_saved_model_paths = {w.name: w for w in path.joinpath(os.environ[FASTNLP_LAUNCH_TIME]).iterdir()}
        epoch_2_path = all_saved_model_paths['trainer-epoch_2']

        callbacks = [StateCallback('new_callback1'), StateCallback('new_callback2')]
        trainer = Trainer(
            model=model_and_optimizers.model,
            driver='torch',
            device='cpu',
            optimizers=model_and_optimizers.optimizers,
            train_dataloader=model_and_optimizers.train_dataloader,
            evaluate_dataloaders=model_and_optimizers.evaluate_dataloaders,
            input_mapping=model_and_optimizers.input_mapping,
            output_mapping=model_and_optimizers.output_mapping,
            metrics=model_and_optimizers.metrics,
            n_epochs=3,
            callbacks=callbacks,
            output_from_new_proc="all"
        )
        trainer.load_checkpoint(folder=epoch_2_path)
        with Capturing() as output:
            trainer.run(num_eval_sanity_batch=0, num_train_batch_per_epoch=2)

        assert 'old_callback1' in output[0]
        assert 'new_callback2' in output[0]
        assert output[0].count('???')==1

    finally:
        rank_zero_rm(path)
        Trainer._custom_callbacks.clear()


@pytest.mark.torch
# 通过自己编写 model_save_fn 和 model_load_fn 来测试 huggingface 的 transformers 的模型的保存和加载；
@pytest.mark.parametrize("driver,device", [("torch", [6, 7]), ("torch", 7)])  # ("torch", "cpu"), ("torch", [0, 1]), ("torch", 1)
@pytest.mark.parametrize("version", [0, 1])
@magic_argv_env_context
@pytest.mark.skip("Skip transformers test for now.")
def test_trainer_checkpoint_callback_2(
    driver,
    device,
    version
):
    path = Path.cwd().joinpath(f"test_model_checkpoint")
    path.mkdir(exist_ok=True, parents=True)

    import transformers  # 版本4.16.2
    import torch
    from torchmetrics import Accuracy
    from transformers import AutoModelForSequenceClassification

    from fastNLP import Trainer
    from torch.optim import AdamW
    from torch.utils.data import DataLoader, Dataset
    from fastNLP.core.utils.utils import dataclass_to_dict
    logger.info(f"transformer version: {transformers.__version__}")
    task = "mrpc"
    model_checkpoint = "distilbert-base-uncased"
    ## Loading the dataset
    from datasets import load_dataset
    actual_task = "mnli" if task == "mnli-mm" else task
    dataset = load_dataset("glue", actual_task)
    # Preprocessing the data
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_checkpoint, use_fast=True)
    task_to_keys = {
        "cola": ("sentence", None),
        "mnli": ("premise", "hypothesis"),
        "mnli-mm": ("premise", "hypothesis"),
        "mrpc": ("sentence1", "sentence2"),
        "qnli": ("question", "sentence"),
        "qqp": ("question1", "question2"),
        "rte": ("sentence1", "sentence2"),
        "sst2": ("sentence", None),
        "stsb": ("sentence1", "sentence2"),
        "wnli": ("sentence1", "sentence2"),
    }
    sentence1_key, sentence2_key = task_to_keys[task]
    if sentence2_key is None:
        print(f"Sentence: {dataset['train'][0][sentence1_key]}")
    else:
        print(f"Sentence 1: {dataset['train'][0][sentence1_key]}")
        print(f"Sentence 2: {dataset['train'][0][sentence2_key]}")
    def preprocess_function(examples):
        if sentence2_key is None:
            return tokenizer(examples[sentence1_key], truncation=True)
        return tokenizer(examples[sentence1_key], examples[sentence2_key], truncation=True)
    encoded_dataset = dataset.map(preprocess_function, batched=True)
    ## Fine-tuning the model
    num_labels = 3 if task.startswith("mnli") else 1 if task == "stsb" else 2
    distilbert_model = AutoModelForSequenceClassification.from_pretrained(model_checkpoint, num_labels=num_labels)

    class TestDistilBertDataset(Dataset):
        def __init__(self, dataset):
            super(TestDistilBertDataset, self).__init__()
            self._dataset = dataset

        def __len__(self):
            return len(self._dataset)

        def __getitem__(self, item):
            _data = self._dataset[item]
            return _data["input_ids"], _data["attention_mask"], [
                _data["label"]]  # , _data["sentence1"], _data["sentence2"]

    def test_bert_collate_fn(batch):
        input_ids, atten_mask, labels = [], [], []
        max_length = [0] * 3
        for each_item in batch:
            input_ids.append(each_item[0])
            max_length[0] = max(max_length[0], len(each_item[0]))
            atten_mask.append(each_item[1])
            max_length[1] = max(max_length[1], len(each_item[1]))
            labels.append(each_item[2])
            max_length[2] = max(max_length[2], len(each_item[2]))

        for i in range(3):
            each = (input_ids, atten_mask, labels)[i]
            for item in each:
                item.extend([0] * (max_length[i] - len(item)))
        return {"input_ids": torch.cat([torch.tensor([item]) for item in input_ids], dim=0),
                "attention_mask": torch.cat([torch.tensor([item]) for item in atten_mask], dim=0),
                "labels": torch.cat([torch.tensor(item) for item in labels], dim=0)}

    test_bert_dataset_train = TestDistilBertDataset(encoded_dataset["train"])
    test_bert_dataloader_train = DataLoader(dataset=test_bert_dataset_train, batch_size=32, shuffle=True,
                                            collate_fn=test_bert_collate_fn)
    test_bert_dataset_validate = TestDistilBertDataset(encoded_dataset["test"])
    test_bert_dataloader_validate = DataLoader(dataset=test_bert_dataset_validate, batch_size=32, shuffle=False,
                                               collate_fn=test_bert_collate_fn)

    def bert_input_mapping(data):
        data["target"] = data["labels"]
        return data

    def bert_output_mapping(data):
        data = dataclass_to_dict(data)
        data["preds"] = torch.max(data["logits"], dim=-1)[1]
        # data["target"] = data["labels"]
        del data["logits"]
        del data["hidden_states"]
        del data["attentions"]
        return data

    test_bert_optimizers = AdamW(params=distilbert_model.parameters(), lr=5e-5)
    test_bert_model = distilbert_model
    acc = Accuracy()

    def model_save_fn(folder):
        test_bert_model.save_pretrained(folder)

    def model_load_fn(folder):
        test_bert_model.from_pretrained(folder)

    if version == 0:
        callbacks = [
            CheckpointCallback(
                monitor="acc",
                folder=path,
                every_n_epochs=None,
                every_n_batches=50,
                topk=None,
                last=False,
                on_exception=None,
                model_save_fn=model_save_fn,
                save_object="trainer"
            )
        ]
    elif version == 1:
        callbacks = [
            CheckpointCallback(
                monitor="acc",
                folder=path,
                every_n_epochs=None,
                every_n_batches=None,
                topk=1,
                last=True,
                on_exception=None,
                model_save_fn=model_save_fn,
                save_object="trainer"
            )
        ]

    try:
        trainer = Trainer(
            model=test_bert_model,
            driver=driver,
            device=device,
            n_epochs=2,
            train_dataloader=test_bert_dataloader_train,
            optimizers=test_bert_optimizers,

            evaluate_dataloaders=test_bert_dataloader_validate,
            input_mapping=bert_input_mapping,
            output_mapping=bert_output_mapping,
            metrics={"acc": acc},

            callbacks=callbacks
        )

        trainer.run()

        all_saved_model_paths = {w.name: w for w in path.joinpath(os.environ[FASTNLP_LAUNCH_TIME]).iterdir()}
        # 检查生成保存模型文件的数量是不是正确的；
        if version == 0:

            if not isinstance(device, list):
                assert "trainer-epoch_1-batch_200" in all_saved_model_paths

                epoch_save_path = all_saved_model_paths["trainer-epoch_1-batch_200"]

                assert len(all_saved_model_paths) == 4
            # ddp 下的文件名不同，因为同样的数据，ddp 用了更少的步数跑完；
            else:
                assert "trainer-epoch_1-batch_100" in all_saved_model_paths

                epoch_save_path = all_saved_model_paths["trainer-epoch_1-batch_100"]

                assert len(all_saved_model_paths) == 2
            all_state_dicts = [epoch_save_path]

        elif version == 1:

            pattern = re.compile("trainer-epoch_[0-9]+-batch_[0-9]+-[a-zA-Z#]+_[0-9]*.?[0-9]*")

            # all_saved_model_paths = {w.name: w for w in path.joinpath(os.environ[FASTNLP_LAUNCH_TIME]).iterdir()}
            if not isinstance(device, list):
                assert "trainer-last" in all_saved_model_paths
                aLL_topk_folders = []
                for each_folder_name in all_saved_model_paths:
                    each_folder_name = pattern.findall(each_folder_name)
                    if len(each_folder_name) != 0:
                        aLL_topk_folders.append(each_folder_name[0])
                assert len(aLL_topk_folders) == 1

                last_save_path = all_saved_model_paths["trainer-last"]
                topk_save_path = all_saved_model_paths[aLL_topk_folders[0]]

                assert len(all_saved_model_paths) == 2
            # ddp 下的文件名不同，因为同样的数据，ddp 用了更少的步数跑完；
            else:
                assert "trainer-last" in all_saved_model_paths

                aLL_topk_folders = []
                for each_folder_name in all_saved_model_paths:
                    each_folder_name = pattern.findall(each_folder_name)
                    if len(each_folder_name) != 0:
                        aLL_topk_folders.append(each_folder_name[0])
                assert len(aLL_topk_folders) == 1

                last_save_path = all_saved_model_paths["trainer-last"]
                topk_save_path = all_saved_model_paths[aLL_topk_folders[0]]

                assert len(all_saved_model_paths) == 2

            all_state_dicts = [last_save_path, topk_save_path]

        for folder in all_state_dicts:
            trainer = Trainer(
                model=test_bert_model,
                driver=driver,
                device=device,
                n_epochs=3,
                train_dataloader=test_bert_dataloader_train,
                optimizers=test_bert_optimizers,

                evaluate_dataloaders=test_bert_dataloader_validate,
                input_mapping=bert_input_mapping,
                output_mapping=bert_output_mapping,
                metrics={"acc": acc},
            )
            trainer.load_checkpoint(folder, model_load_fn=model_load_fn)

            trainer.run()
            trainer.driver.barrier()

    finally:
        rank_zero_rm(path)
        # pass

    if dist.is_initialized():
        dist.destroy_process_group()



