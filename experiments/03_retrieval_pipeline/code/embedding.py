import torch,numpy as np
from transformers import AutoModel,AutoTokenizer
from common import *
class CPUEmbedder:
 def __init__(self):
  torch.manual_seed(42);torch.set_num_threads(1);torch.use_deterministic_algorithms(True)
  self.path=BASE/'LLM-Model/all-MiniLM-L6-v2'
  self.tokenizer=AutoTokenizer.from_pretrained(self.path,local_files_only=True)
  self.model=AutoModel.from_pretrained(self.path,local_files_only=True).eval().to('cpu')
  pooling=read(self.path/'1_Pooling/config.json');assert pooling['pooling_mode_mean_tokens'] and not pooling['pooling_mode_cls_token']
  assert read(self.path/'sentence_bert_config.json')['max_seq_length']==256
 def encode(self,texts,batch_size=24):
  arr=[]
  with torch.inference_mode():
   for i in range(0,len(texts),batch_size):
    t=self.tokenizer(texts[i:i+batch_size],padding=True,truncation=True,max_length=256,return_tensors='pt')
    h=self.model(**t).last_hidden_state;mask=t['attention_mask'].unsqueeze(-1)
    v=(h*mask).sum(dim=1)/mask.sum(dim=1).clamp(min=1)
    arr.append(torch.nn.functional.normalize(v,p=2,dim=1).numpy().astype('float32'))
  return np.concatenate(arr)
