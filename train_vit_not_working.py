# https://github.com/huggingface/blog/blob/main/fine-tune-vit.md
# https://huggingface.co/blog/fine-tune-vit
import pandas as pd
from torch.utils.data import DataLoader, WeightedRandomSampler
from torch import nn
from transformers import ViTFeatureExtractor, ViTForImageClassification
from sklearn.metrics import accuracy_score, f1_score, classification_report
import torch
from torch.utils.data import Dataset
import cv2
import matplotlib.pyplot as plt
import os
from sklearn.model_selection import train_test_split
from tqdm import tqdm

PATH = "/home/ubuntu/Final-Project-Group4"
EXCEL_PATH = PATH + os.path.sep + "dataset" + os.path.sep + "final_dataset.xlsx"
DATA_DIR = PATH + os.path.sep + "dataset" + os.path.sep + "train" + os.path.sep

df = pd.read_excel(EXCEL_PATH)
one_hot_encoded = df["Category"].str.get_dummies(sep=",")
df["target_class"] = one_hot_encoded.apply(lambda x: ",".join(x.astype(str)), axis=1)
train_data, test_data = train_test_split(df, test_size=0.20, random_state=42)


class MultiLabelImageDataset(Dataset):
    def __init__(self, list_IDs, type_data, feature_extractor):
        self.type_data = type_data
        self.list_IDs = list_IDs
        self.feature_extractor = feature_extractor

    def __len__(self):
        return len(self.list_IDs)

    def __getitem__(self, index):
        ID = self.list_IDs[index]

        if self.type_data == "train":
            y = train_data.target_class.get(ID)
            file = DATA_DIR + train_data.ImageId.get(ID)
        else:
            y = test_data.target_class.get(ID)
            file = DATA_DIR + test_data.ImageId.get(ID)

        y = y.split(",")
        labels_ohe = [int(e) for e in y]
        y = torch.FloatTensor(labels_ohe)

        img = cv2.imread(file)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        img = self.feature_extractor(img, return_tensors="pt")["pixel_values"].squeeze()

        return img, y


feature_extractor = ViTFeatureExtractor.from_pretrained("google/vit-base-patch16-224")

train_dataset = MultiLabelImageDataset(
    list_IDs=train_data.index,
    type_data="train",
    feature_extractor=feature_extractor,
)
test_dataset = MultiLabelImageDataset(
    list_IDs=test_data.index,
    type_data="test",
    feature_extractor=feature_extractor,
)

device = "cuda:0" if torch.cuda.is_available() else "cpu"
label_matrix = train_data["Category"].str.get_dummies(",")
class_frequencies = label_matrix.sum()
total_samples = class_frequencies.sum()
class_weights = total_samples / (class_frequencies * len(class_frequencies))
class_weight_tensor = torch.tensor(class_weights.values, dtype=torch.float)
sample_weights = label_matrix.dot(class_weight_tensor).values
sampler = WeightedRandomSampler(
    sample_weights, num_samples=len(train_dataset), replacement=True
)

train_loader = DataLoader(train_dataset, batch_size=64, sampler=sampler, num_workers=6)
validation_loader = DataLoader(test_dataset, batch_size=64, num_workers=6)

num_classes = 46
model = ViTForImageClassification.from_pretrained(
    "google/vit-base-patch16-224",
    num_labels=num_classes,
    id2label={str(i): str(i) for i in range(num_classes)},
    label2id={str(i): str(i) for i in range(num_classes)},
    problem_type="multi_label_classification",
    ignore_mismatched_sizes=True,
)

criterion = nn.BCEWithLogitsLoss()
optimizer = torch.optim.AdamW(model.parameters(), lr=1e-5)
scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
    optimizer, mode="max", factor=0.1, patience=2, verbose=True
)

num_epochs = 6
model.to(device)
best_val_f1_macro = 0

train_losses = []
val_losses = []
val_f1_micros = []
val_f1_macros = []

for epoch in range(num_epochs):
    model.train()
    train_loss = 0.0
    steps_train = 0.0
    with tqdm(total=len(train_loader), desc="Epoch {}".format(epoch)) as pbar:
        for images, labels in train_loader:
            images = images.to(device)
            labels = labels.to(device)

            optimizer.zero_grad()
            outputs = model(images).logits
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            steps_train += 1
            train_loss += loss.item()
            pbar.update(1)
            pbar.set_postfix_str("Train Loss: {:.5f}".format(train_loss / steps_train))
    train_loss /= len(train_dataset)
    train_losses.append(train_loss)

    model.eval()
    val_loss = 0.0
    steps_test = 0.0
    predictions = []
    true_labels = []

    with torch.no_grad():
        with tqdm(total=len(validation_loader), desc="Epoch {}".format(epoch)) as pbar:
            for images, labels in validation_loader:
                images = images.to(device)
                labels = labels.to(device)
                outputs = model(images).logits
                loss = criterion(outputs, labels)
                val_loss += loss.item()
                predictions.extend(outputs.sigmoid().cpu().numpy() >= 0.5)
                true_labels.extend(labels.cpu().numpy())
                steps_test += 1
                pbar.update(1)
                pbar.set_postfix_str("Test Loss: {:.5f}".format(val_loss / steps_test))

    val_loss /= len(test_dataset)
    val_losses.append(val_loss)

    f1_micro = f1_score(true_labels, predictions, average="micro")
    f1_macro = f1_score(true_labels, predictions, average="macro")
    f1_scores = f1_score(true_labels, predictions, average=None)
    val_accuracy_score = accuracy_score(true_labels, predictions)
    val_f1_micros.append(f1_micro)
    val_f1_macros.append(f1_macro)

    print(
        f"Epoch [{epoch+1}/{num_epochs}], "
        f"Train Loss: {train_loss:.4f}, "
        f"Val Loss: {val_loss:.4f}, "
        f"Val F1 Micro: {f1_micro:.4f}, "
        f"Val F1 Macro: {f1_macro:.4f}, "
        f"Val Accuracy: {val_accuracy_score:.4f}"
    )
    print("Class-wise F1 Scores:")
    for i, score in enumerate(f1_scores):
        print(f"Class {i+1}: {score:.4f}")

    if f1_macro > best_val_f1_macro:
        best_val_f1_macro = f1_macro
        best_epoch = epoch + 1
        torch.save(
            model.state_dict(),
            f"model_vit_base_patch16_224.pt",
        )

    print(classification_report(true_labels, predictions))
    scheduler.step(f1_macro)

print(f"Best Validation F1 Macro: {best_val_f1_macro:.4f} (Epoch {best_epoch})")

plt.figure(figsize=(10, 5))
plt.plot(range(1, num_epochs + 1), train_losses, label="Training Loss")
plt.plot(range(1, num_epochs + 1), val_losses, label="Validation Loss")
plt.xlabel("Epoch")
plt.ylabel("Loss")
plt.legend()
plt.savefig("vit_loss_plot.png")
plt.show()
