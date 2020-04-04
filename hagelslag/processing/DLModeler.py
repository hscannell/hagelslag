from datetime import timedelta
from glob import glob
import pandas as pd
import numpy as np
import random
import h5py
import os

from sklearn.utils import shuffle

from dask.distributed import Client, progress
import dask.array as da
import dask

from keras.optimizers import SGD, Adam
from keras.regularizers import l2
import keras.backend as K
from keras import layers
from keras import models

class DLModeler(object):
    def __init__(self,model_path,hf_path,start_dates,end_dates,
        num_examples,class_percentage,patch_radius,run_date_format,forecast_variables):
        
        self.model_path = model_path
        self.hf_path = hf_path
        self.start_dates = start_dates
        self.end_dates = end_dates
        self.num_examples = num_examples
        self.class_percentage = class_percentage
        self.patch_radius = patch_radius
        self.run_date_format = run_date_format
        self.forecast_variables = forecast_variables
        return

    def train_models(self,member,mode='train'):
        print()
        
        #Selecting random patches for training
        training_filename = self.model_path+'/{0}_{1}_{2}_{3}_training_examples.csv'.format(
            member, self.start_dates[mode].strftime('%Y%m%d'),self.end_dates[mode].strftime('%Y%m%d'),
            self.num_examples)
        if os.path.exists(training_filename):
            print('\nOpening {0}\n'.format(training_filename))
            patches = pd.read_csv(training_filename,index_col=0)
        else:
            patches = self.training_data_selection(member,training_filename)
        
        #Creating label data of shape (# examples, #classes)
        unique_obs_label = np.unique(patches['Obs Label'])
        member_obs_label = np.zeros( (len(patches['Obs Label']),len(unique_obs_label)) )
        for l, label in enumerate(patches['Obs Label']):
            for u,unique in enumerate(unique_obs_label):
                if label == unique:
                    member_obs_label[l,u] = 1.0 

        #Extract model patch data
        member_model_data = self.reading_files(mode,member,patches['Random Date'], 
            patches['Random Hour'],patches['Random Patch'],patches['Data Augmentation'])
        #Standardize data
        standard_model_data = self.standardize(member,member_model_data) 
        #Train and save models
        self.train_CNN(member,standard_model_data,member_obs_label)    
        return 
    
    def create_forecasts(self,member,map_file):
        #open CNN model 
        cnn_model_file = self.model_path+'/{0}_{1}_{2}_CNN_model.h5'.format(member,
            self.start_dates['train'].strftime('%Y%m%d'),self.end_dates['train'].strftime('%Y%m%d'))
        cnn_model = models.load_model(cnn_model_file) #"../models/conv_model.h5", custom_objects={"brier_skill_score_keras":brier_skill_score_keras})
        
        forecast_dates = pd.date_range(start=self.start_dates['forecast'],
            end=self.end_dates['forecast'],freq='1D').strftime(self.run_date_format)
        
        for date in forecast_dates:
            print('\nPredicting {0} data\n'.format(date))
            member_model_data = self.reading_files('forecast',member,[date])
            #Still in the form (#variables, #hours, #patches, nx, ny)
            patch_predictions = np.empty( (2,member_model_data.shape[1], member_model_data.shape[2]) )*np.nan
            var_shape = (member_model_data.shape[2], member_model_data.shape[3],
                        member_model_data.shape[4],member_model_data.shape[0])
            for hour in np.arange(member_model_data.shape[1]):
                forecast_data = member_model_data[:,hour,:,:,:].ravel().reshape(var_shape)
                standard_forecast_data = self.standardize(member,forecast_data)
                preds = cnn_model.predict(forecast_data)[:,:]
                print(preds[:,2])
                print(preds[:,3])
                print(np.shape(preds[:,2]))
                patch_predictions[0,hour,:] = preds[:,2]
                patch_predictions[1,hour,:] = preds[:,3]
                break
        return
    
    def gridded_forecasts(self,patch_predictions,map_file):
        return 
        #use ckd tree to put the forecasts on a map?
        #return one member map forecasts over 24 hours
        #average together to create ensemble forecast


    def training_data_selection(self,member,training_filename):
        string_dates = pd.date_range(start=self.start_dates['train'],
                    end=self.end_dates['train'],freq='1D').strftime(self.run_date_format)
        
        #Place all obs data into respective category
        all_date_obs_catetories = {}
        #Loop through each category:
        for category in [0,1,2,3]:
            single_date_obs = {}
            #Loop through each date
            for str_date in string_dates:
                #If there are model or obs files, continue to next date
                model_file = glob(self.hf_path + '/{0}/*{1}*'.format(member,str_date))
                obs_file = glob(self.hf_path + '/*obs*{0}*'.format(str_date))
                if len(model_file) < 1 or len(obs_file) < 1:continue
                #Open obs file
                data = h5py.File(obs_file[0], 'r')['labels'][()]
                hourly_data = {}
                for hour in np.arange(data.shape[0]):
                    inds = np.where(data[hour] == category)[0]
                    if len(inds) >1: hourly_data[hour] = inds 
                if hourly_data: single_date_obs[str_date] = hourly_data
            if single_date_obs: all_date_obs_catetories[category] = single_date_obs
        
        #Find the number of desired examples per category
        num_examples_obs_categories = [] 
        for class_label,percentage in self.class_percentage.items():
            subset_class_examples = int(self.num_examples*percentage)
            total_class_examples = all_date_obs_catetories[class_label]
            if len(total_class_examples) < subset_class_examples:data_augment = 1
            else:data_augment = 0
            for e in np.arange(subset_class_examples):
                random_date = np.random.choice(list(total_class_examples))
                random_hour = np.random.choice(list(total_class_examples[random_date]))
                random_patch = np.random.choice(list(total_class_examples[random_date][random_hour]))
                num_examples_obs_categories.append([random_date,random_hour,random_patch,class_label,data_augment])
        
        #Output dataframe with the randomly chosen data
        cols = ['Random Date','Random Hour', 'Random Patch', 'Obs Label','Data Augmentation'] 
        pandas_df_examples = pd.DataFrame(num_examples_obs_categories,columns=cols)
        pandas_df_examples = shuffle(pandas_df_examples)
        pandas_df_examples.reset_index(inplace=True, drop=True)
        print(pandas_df_examples)
        print('\nWriting to {0}\n'.format(training_filename))
        pandas_df_examples.to_csv(training_filename)
        return pandas_df_examples 
    
    def reading_files(self,mode,member,dates,hour=None,patch=None,data_augment=None): 
        patch_data = []
        client = Client(threads_per_worker=4, n_workers=10) 
        print('\nReading member files\n')
        for d,date in enumerate(dates):
            model_files = [glob(self.hf_path + '/{0}/*{1}*{2}*.h5'.format(member,variable,date))[0] for variable in self.forecast_variables]
            if mode == 'train':
                if d%500 == 0:print(d,date)
                patch_data.append(dask.delayed(self.extracting_patch_data)(model_files,hour[d],patch[d],data_augment[d])) 
            elif mode =='forecast': patch_data.append(self.extracting_patch_data(model_files))
        if mode =='forecast': 
            return np.array(patch_data)[0]
        member_model_data = dask.compute(patch_data)[0]
        return np.array(member_model_data) 

    def extracting_patch_data(self,model_files,hour=None,patch=None,data_augment=None):
        if data_augment is None:patch_data=[]
        else:patch_data = np.zeros( (self.patch_radius,self.patch_radius, len(self.forecast_variables)) ) 
        for v,variable_file in enumerate(model_files):
            if len(variable_file) <= 1:continue
            hf = h5py.File(variable_file, 'r')
            if data_augment is None:patch_data.append(hf['patches'][()])
            else:
                if data_augment > 0.5:
                    variable_data = hf['patches'][hour,patch,:,:].ravel()
                    noise = np.nanvar(variable_data)*np.random.choice(np.arange(-0.5,0.5,0.15))
                    patch_data[:,:,v] = (variable_data + noise).reshape((self.patch_radius,self.patch_radius))
                else: patch_data[:,:,v] = hf['patches'][hour,patch,:,:]
            hf.close()
        return np.array(patch_data)


    def standardize(self,member,model_data):
        scaling_file = self.model_path+'/{0}_{1}_{2}_{3}_training_scaling_values.csv'.format(
            member,self.start_dates['train'].strftime('%Y%m%d'),
            self.end_dates['train'].strftime('%Y%m%d'),self.num_examples)

        standard_model_data = np.ones(np.shape(model_data))*np.nan
        if not os.path.exists(scaling_file):
            scaling_values = pd.DataFrame(np.zeros((len(self.forecast_variables), 2), 
                dtype=np.float32),index=self.forecast_variables,columns=['mean','std'])
        else:
            print('\nOpening {0}\n'.format(scaling_file))
            scaling_values = pd.read_csv(scaling_file,index_col=0)
        #Standardizing data
        for n in range(len(self.forecast_variables)):
            scaling_values.loc[n,'mean'] = np.nanmean(model_data[:,:,:,n])
            scaling_values.loc[n,'std'] = np.nanstd(model_data[:,:,:,n])
            standard_model_data[:,:,:,n] = (model_data[:,:,:,n]-scaling_values['mean'][n])/scaling_values['std'][n] 
        #Output training scaling values
        if not os.path.exists(scaling_file):
            print('\nWriting to {0}\n'.format(scaling_file))
            scaling_values.to_csv(scaling_file)
        del model_data,scaling_values
        return standard_model_data

    def train_CNN(self,member,model_data,model_labels):

        print('\nTraining {0} models'.format(member))
        print('Model data shape {0}'.format(np.shape(model_data)))
        print('Label data shape {0}\n'.format(np.shape(model_labels)))

        #Initiliaze Convolutional Neural Net (CNN)
        model = models.Sequential()
        l2_a= 0.001

        #First layer, input shape (y,x,# variables) 
        model.add(layers.Conv2D(32, (3, 3), activation='relu', 
            padding="same", kernel_regularizer=l2(l2_a),
            input_shape=(np.shape(model_data[0]))))
        #model.add(layers.Dropout(0.1))
        model.add(layers.MaxPooling2D((2, 2))) 
        #Second layer
        model.add(layers.Conv2D(64, (3, 3), activation='relu',padding="same", kernel_regularizer=l2(l2_a)))
        #model.add(layers.Dropout(0.1))
        model.add(layers.MaxPooling2D((2, 2)))
        #Third layer
        model.add(layers.Conv2D(64, (3, 3), activation='relu',padding="same", kernel_regularizer=l2(l2_a)))
        #model.add(layers.Dropout(0.1))
        model.add(layers.MaxPooling2D((2, 2)))
    
        #Flatten the last convolutional layer into a long feature vector
        model.add(layers.Flatten())
        model.add(layers.Dense(64, activation='relu'))
        model.add(layers.Dense(4, activation='sigmoid'))

        opt = Adam()
        model.compile(optimizer=opt,loss='categorical_crossentropy',metrics=['acc'])
        batches = int(self.num_examples/30.0)
        print(batches)
        conv_hist = model.fit(model_data, model_labels, epochs=30, batch_size=batches,validation_split=0.1)
        model_file = self.model_path+'/{0}_{1}_{2}_CNN_model.h5'.format(member,
            self.start_dates['train'].strftime('%Y%m%d'),self.end_dates['train'].strftime('%Y%m%d'))
        model.save(model_file)
        print('\nWriting out {0}\n'.format(model_file))
        del model_labels,model_data
        return 

def brier_skill_score_keras(obs, preds):
    bs = k.mean((preds - obs) ** 2)
    climo = k.mean((obs - k.mean(obs)) ** 2)
    return 1.0 - (bs/climo)
