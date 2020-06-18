import torch
import torch.nn.functional as F
from torch.autograd import Variable

import pandas as pd
import math
import os
import configure as c

from DB_wav_reader import read_feats_structure
from SR_Dataset import read_MFB, ToTensorTestInput
from model.model import background_resnet

def load_model(use_cuda, log_dir, cp_num, embedding_size, n_classes,runMode):
    model = background_resnet(embedding_size=embedding_size, num_classes=n_classes, mode=runMode)
    if use_cuda:
        model.cuda()
    print('=> loading checkpoint')
    # original saved file with DataParallel
    checkpoint = torch.load(log_dir + '/checkpoint_' + str(cp_num) + '.pth',  map_location=torch.device('cpu'))
    # create new OrderedDict that does not contain `module.`
    model.load_state_dict(checkpoint['state_dict'])
    model.eval()
    return model

def split_enroll_and_test(dataroot_dir):
    DB_all = read_feats_structure(dataroot_dir)
    enroll_DB = pd.DataFrame()
    test_DB = pd.DataFrame()
    
    enroll_DB = DB_all[DB_all['filename'].str.contains('enroll.p')]
    test_DB = DB_all[DB_all['filename'].str.contains('test.p')]
    
    # Reset the index
    enroll_DB = enroll_DB.reset_index(drop=True)
    test_DB = test_DB.reset_index(drop=True)
    return enroll_DB, test_DB

def load_enroll_embeddings(embedding_dir):
    embeddings = {}
    for f in os.listdir(embedding_dir):
        spk = f.replace('.pth','')
        # Select the speakers who are in the 'enroll_spk_list'
        embedding_path = os.path.join(embedding_dir, f)
        tmp_embeddings = torch.load(embedding_path,  map_location=torch.device('cpu'))
        embeddings[spk] = tmp_embeddings
        
    return embeddings

def get_embeddings(use_cuda, filename, model, test_frames):
    input,label= read_MFB(filename) # input size:(n_frames, n_dims)
    
    tot_segments = math.ceil(len(input)/test_frames) # total number of segments with 'test_frames' 
    activation = 0
    with torch.no_grad():
        for i in range(tot_segments):
            temp_input = input[i*test_frames:i*test_frames+test_frames]
            
            TT = ToTensorTestInput()
            temp_input = TT(temp_input) # size:(1, 1, n_dims, n_frames)
    
            if use_cuda:
                temp_input = temp_input.cuda()
            temp_activation,_ = model(temp_input)
            activation += torch.sum(temp_activation[1], dim=0, keepdim=True)
    
    activation = l2_norm(activation, 1)
                
    return activation

def l2_norm(input, alpha):
    input_size = input.size()  # size:(n_frames, dim)
    buffer = torch.pow(input, 2)  # 2 denotes a squared operation. size:(n_frames, dim)
    normp = torch.sum(buffer, 1).add_(1e-10)  # size:(n_frames)
    norm = torch.sqrt(normp)  # size:(n_frames)
    _output = torch.div(input, norm.view(-1, 1).expand_as(input))
    output = _output.view(input_size)
    # Multiply by alpha = 10 as suggested in https://arxiv.org/pdf/1703.09507.pdf
    output = output * alpha
    return output

def perform_identification(use_cuda, model, embeddings, test_filename, test_frames, test_speaker, spk_list):
    test_embedding = get_embeddings(use_cuda, test_filename, model, test_frames)
    max_score = -10**8
    best_spk = None
    for spk in spk_list:
        score = F.cosine_similarity(test_embedding, embeddings[spk])
        score = score.data.cpu().numpy() 
        if score > max_score:
            max_score = score
            best_spk = spk
    print("Speaker identification result : %s" %best_spk)
    # true_spk = test_filename.split('/')[-2].split('_')[0]
    true_spk=test_speaker
    print("\n=== Speaker identification ===")
    print("True speaker : %s\nPredicted speaker : %s\nResult : %s\n" %(true_spk, best_spk, true_spk==best_spk))
    return best_spk

def main():
    
    log_dir = 'model_saved' # Where the checkpoints are saved
    embedding_dir = 'enroll_embeddings_TIMIT' # Where embeddings are saved
    test_dir = '/home/usaranya63/speaker-identification/test_voxceleb_final/' # Where test features are saved
    
    # Settings
    use_cuda = False # Use cuda or not
    embedding_size = [256, 128, 256]# Dimension of speaker embeddings
    cp_num = 27 # Which checkpoint to use?
    n_classes = 220 # How many speakers in training data?
    test_frames = 100 # Split the test utterance 
    mode='i'
    # Load model from checkpoint
    model = load_model(use_cuda, log_dir, cp_num, embedding_size, n_classes, mode)

    # Get the dataframe for test DB
    enroll_DB, test_DB = split_enroll_and_test(c.TEST_FEAT_DIR)
    
    # Load enroll embeddings
    embeddings = load_enroll_embeddings(embedding_dir)
    # print(embeddings)
    """ Test speaker list
    '103F3021', '207F2088', '213F5100', '217F3038', '225M4062', 
    '229M2031', '230M4087', '233F4013', '236M3043', '240M3063'
    """ 
    
    # spk_list = ['103F3021', '207F2088', '213F5100', '217F3038', '225M4062',\
    # '229M2031', '230M4087', '233F4013', '236M3043', '240M3063']
    # spk_list=['1','2','3','4','5','6','7','8','9','10','11','12','13','14','15','16']
    CR=0
    result = pd.read_csv('/home/usaranya63/speaker-identification/voxceleb_enroll_test.csv', delimiter=",")
    filenames=result['filename']
    speakers=result['speaker']
    spk_list=[]
    for i in speakers:
        spk_list.append(i)
    spk_list=list(set(spk_list))
    spk_list.sort()
    count=0
    for i in range(len(speakers)):
        if i%50 !=0: 
            filepath='test'+str(i)+'.p'
            test_path = os.path.join(test_dir, speakers[i], filepath)
            count+=1
            # Perform the test
            print("i :", str(i))
            if i!=1378:
                best_spk = perform_identification(use_cuda, model, embeddings, test_path, test_frames,speakers[i],spk_list)
                if speakers[i]==best_spk:
                    CR+=1
    print(CR)
    print(count)
    accuracy=(CR/(len(spk_list)*count))*100
    print("Accuracy : ", str(accuracy))
    file1 = open("output/output_identification_15_reduced2.txt", "a")
    file1.write("Accuracy %s " %(accuracy))
    file1.close()
    
    # Set the test speaker
    # test_speaker = '3'
    
    # test_path = os.path.join(test_dir, test_speaker, 'test.p')
    # test_path='test.p'
    
    # Perform the test 
    # best_spk = perform_identification(use_cuda, model, embeddings, test_path, test_frames, spk_list)

if __name__ == '__main__':
    main()