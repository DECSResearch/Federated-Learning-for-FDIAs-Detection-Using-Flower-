import os
import joblib
import argparse
import flwr as fl
import numpy as np
import pandas as pd
import random
import tensorflow as tf
from typing import Tuple
from keras.models import Sequential
from keras.layers import LSTM, RepeatVector, TimeDistributed, Dense
import warnings

warnings.simplefilter('ignore')

# Argument Parser
parser = argparse.ArgumentParser()
parser.add_argument('--ip', help='Provide the IP address', default="0.0.0.0", required=False)
parser.add_argument('--port', help='Provide the Port address', default="8080", required=False)
parser.add_argument('--id', help='Provide the client id', default="1", required=True)
parser.add_argument('--folder', help='Provide the Dataset folder', default='Client_1_RF', type=str, required=False)
args = parser.parse_args()

# Constants
SERVER_ADDR = f"{args.ip}:{args.port}"
FOLDER_LOC = args.folder
CLIENT_ID = args.id

temp_loss = []
temp_mape = []

# Set random seed for reproducibility
np.random.seed(42)
random.seed(42)
tf.random.set_seed(42)

# Load Dataset Function
def load_dataset():
    folder_path = os.path.join('.', 'data', FOLDER_LOC)
    for filename in os.listdir(folder_path):
        if filename.endswith('.csv'):
            file_path = os.path.join(folder_path, filename)
            dataframe = pd.read_csv(file_path)
            dataframe['datetimestamp'] = pd.to_datetime(dataframe['datetimestamp']) # Convert 'datetimestamp' column to datetime
            df = dataframe[['datetimestamp', 'Hz_mod_anomaly', 'mod_BIN']]  # Take only 'datetimestamp', 'Hz_mod_anomaly', and 'mod_BIN' columns
            df.set_index('datetimestamp', inplace=True)  # Set 'datetimestamp' as index
    print("First few rows of the DataFrame:")
    print(df.head())
    print("Column names:")
    print(df.columns)
    return df


# Preprocess Dataset Function
def preprocess_dataset(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    # Print start and end date
    print("start date of dataset is :", df.index.min())
    print("end date of dataset is :", df.index.max())

    # Separate normal data (label 0) from anomalous data
    normal_data = df[df['mod_BIN'] == 0]
    anomalous_data = df[df['mod_BIN'] != 0]

    # Print start and end dates of normal data
    print("Start date of normal data is:", normal_data.index.min())
    print("End date of normal data is:", normal_data.index.max())

    # Print start and end dates of anomalous data
    print("Start date of anomalous data is:", anomalous_data.index.min())
    print("End date of anomalous data is:", anomalous_data.index.max())

    # Now we make train and test dataset
    # in this case 80 % of normal data would be used for training and rest 20% of normal data + all anomalous data would be used in test
    # Calculate the number of rows for 80% of normal data
    train_normal_size = int(0.8 * len(normal_data))

    # Select the first 80% of normal data for training
    train = normal_data.iloc[:train_normal_size]

    # Select the remaining 20% of normal data for testing
    test_normal = normal_data.iloc[train_normal_size:]

    # Concatenate test normal data with anomalous data
    test = pd.concat([test_normal, anomalous_data])

    # Print the shapes of train and test sets
    print("Shape of train:", train.shape)
    print("Shape of test:", test.shape)

    # Drop the 'mod_BIN' column from train and test DataFrames because it was just labels to split between train and test
    train = train.drop(columns=['mod_BIN'])
    test = test.drop(columns=['mod_BIN'])

    seq_size = 20  # Number of time steps to look back
    # larger sequence size (look further back) may improve forecasting
    def to_sequence(x, y, seq_size=1):
        x_values = []
        y_values = []

        for i in range(len(x) - seq_size):
            x_values.append(x.iloc[i:(i + seq_size)].values)
            y_values.append(y.iloc[i + seq_size])

        return np.array(x_values), np.array(y_values)

    X_train, y_train = to_sequence(train[['Hz_mod_anomaly']], train['Hz_mod_anomaly'], seq_size)
    X_test, y_test = to_sequence(test[['Hz_mod_anomaly']], test['Hz_mod_anomaly'], seq_size)

    print("train X shape", X_train.shape)
    print("train Y shape", y_train.shape)
    print("test X shape", X_test.shape)
    print("test Y shape", y_test.shape)

    return X_train, y_train, X_test, y_test


class FlowerClient(fl.client.NumPyClient):
    def __init__(self):
        self.X_train = None
        self.y_train = None
        self.X_test = None
        self.y_test = None
        self.model = None

    def get_parameters(self, config):
        return model.get_weights()
    def fit(self, parameters, config):
        model.set_weights(parameters)
        r= self.model.fit(self.X_train, self.y_train, epochs=5, batch_size=100, validation_split=0.2, verbose=1)
        hist = r.history
        print("Fit history : ", hist)
        return self.model.get_weights(), len(self.X_train), {}

    def evaluate(self, parameters, config):
        model.set_weights(parameters)
        # loss = self.model.evaluate(self.X_test, self.y_test, verbose=0)
        eval_loss, eval_mape = self.model.evaluate(X_test, y_test)

        temp_loss.append(eval_loss)
        temp_mape.append(eval_mape)

        print(f"Eval Loss: {eval_loss} || Eval MAPE: {eval_mape}")
        return eval_loss, len(X_test), {"mape": eval_mape}

        # # Compute mae separately
        # train_predict = self.model.predict(self.X_test)
        # test_mae = np.mean(np.abs(train_predict - self.X_test), axis=1)
        # mean_test_mae = np.mean(test_mae)
        # print("Loss:", loss)
        # print("Mean Training MAE:", mean_test_mae)
        # return loss, len(X_train), {"mean_train_mae": mean_test_mae}


# Main Function
if __name__ == "__main__":
    # Load Dataset
    df = load_dataset()
    # Preprocess/split Dataset
    X_train, y_train, X_test, y_test = preprocess_dataset(df)

    # Create and Compile Model
    model = Sequential()
    model.add(LSTM(128, activation='relu', input_shape=(X_train.shape[1], X_train.shape[2]), return_sequences=True))
    model.add(LSTM(64, activation='relu', return_sequences=False))
    model.add(RepeatVector(X_train.shape[1]))
    model.add(LSTM(64, activation='relu', return_sequences=True))
    model.add(LSTM(128, activation='relu', return_sequences=True))
    model.add(TimeDistributed(Dense(X_train.shape[2])))
    model.compile(optimizer='adam', loss='mae', metrics=["mape"])

    # Create Flower Client
    flower_client = FlowerClient()
    flower_client.X_train = X_train
    flower_client.y_train = y_train
    flower_client.X_test = X_test
    flower_client.y_test = y_test
    flower_client.model = model

    # Start Client
    fl.client.start_numpy_client(server_address=SERVER_ADDR, client=flower_client)

    # Save the trained model to a file
    joblib.dump(model, 'trained_model_LSTM.joblib')