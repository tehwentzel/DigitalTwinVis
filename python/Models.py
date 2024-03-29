import torch
# import numpy as np
import pandas as pd
from Constants import Const
from captum.attr import IntegratedGradients



class SimulatorBase(torch.nn.Module):
    
    def __init__(self,
                 input_size,
                 hidden_layers = [1000],
                 dropout = 0.5,
                 input_dropout=0.1,
                 state = 1,
                 eps = 0.01,
                ):
        #predicts disease state (sd, pr, cr) for primar and nodal, then dose modications or cc type (depending on state), and [dlt ratings]
        torch.nn.Module.__init__(self)
        self.state = state
        self.input_dropout = torch.nn.Dropout(input_dropout)
        
        first_layer =torch.nn.Linear(input_size,hidden_layers[0],bias=True)
        layers = [first_layer,torch.nn.ReLU()]
        curr_size = hidden_layers[0]
        for ndim in hidden_layers[1:]:
            layer = torch.nn.Linear(curr_size,ndim)
            curr_size = ndim
            layers.append(layer)
            layers.append(torch.nn.ReLU())
        self.layers = torch.nn.ModuleList(layers)
        self.batchnorm = torch.nn.BatchNorm1d(hidden_layers[-1])
        self.dropout = torch.nn.Dropout(dropout)
        self.relu = torch.nn.Softplus()
    
        input_mean = torch.tensor([0])
        input_std = torch.tensor([1])
        self.eps = eps
        self.register_buffer('input_mean', input_mean)
        self.register_buffer('input_std',input_std)
        
        self.sigmoid = torch.nn.Sigmoid()
        self.softmax = torch.nn.LogSoftmax(dim=1)
        self.identifier = 'state'  +str(state) + '_input'+str(input_size) + '_dims' + ','.join([str(h) for h in hidden_layers]) + '_dropout' + str(input_dropout) + ',' + str(dropout)
        
    def normalize(self,x):
        x = (x - self.input_mean + self.eps)/(self.input_std + self.eps)
        return x
    
    def fit_normalizer(self,x):
        input_mean = x.mean(axis=0)
        input_std = x.std(axis=0)
        self.register_buffer('input_mean', input_mean)
        self.register_buffer('input_std',input_std)
        return True
    
    def get_attributions(self,x,output=-1,target=0):
        if output == -1:
            model = self
        else:
            model = lambda x: self.forward(x)[output]
        ig = IntegratedGradients(model)
        if isinstance(x,torch.Tensor):
            base = torch.zeros(x.shape)
        else:
            base = tuple([torch.zeros(xx.shape) for xx in x])
        attributions = ig.attribute(x,base,target=target)
        return attributions

class SimulatorAttentionBase(SimulatorBase):
    
    def __init__(self,input_size,
                 hidden_layers = [100],
                 attention_heads=[2], 
                 embed_size=100,
                 **kwargs,
                ):
        
        super().__init__(input_size,hidden_layers=hidden_layers,**kwargs)
        if embed_size == 0:
            attention_heads[0] = 1
            self.resize_layer = lambda x: x
        else:
            self.resize_layer = torch.nn.Linear(input_size,embed_size)
        #overrite layer intitialization
        layers = []
        attentions = []
        norms = []
        curr_size = embed_size
        i = 0
        for aheads,lindim in zip(attention_heads,hidden_layers):
            attention = torch.nn.MultiheadAttention(curr_size,aheads)
            linear = torch.nn.Linear(curr_size,lindim)
            norm = torch.nn.LayerNorm(curr_size)
            layers.append(linear)
            attentions.append(attention)
            norms.append(norm)
            curr_size = lindim
            
        self.layers = torch.nn.ModuleList(layers)
        self.attentions = torch.nn.ModuleList(attentions)
        self.norms = torch.nn.ModuleList(norms)
        self.final_layer = torch.nn.Linear(hidden_layers[-1],len(Const.decisions))
        self.activation = torch.nn.ReLU()

class OutcomeSimulator(SimulatorBase):
    
    def __init__(self,
                 input_size,
                 hidden_layers = [500,500],
                 dropout = 0.7,
                 input_dropout=0.1,
                 state = 1,
                ):
        #predicts disease state (sd, pr, cr) for primar and nodal, then dose modications or cc type (depending on state), and [dlt ratings]
        super().__init__(input_size,hidden_layers=hidden_layers,dropout=dropout,input_dropout=input_dropout,state=state)
    
        self.disease_layer = torch.nn.Linear(hidden_layers[-1],len(Const.primary_disease_states))
        self.nodal_disease_layer = torch.nn.Linear(hidden_layers[-1],len(Const.nodal_disease_states))
        #dlt ratings are 0-4 even though they don't always appear
        self.dlt_layers = torch.nn.ModuleList([torch.nn.Linear(hidden_layers[-1],1) for i in Const.dlt1])
        assert( state in [1,2])
        if state == 1:
#             self.dlt_layers = torch.nn.ModuleList([torch.nn.Linear(hidden_layers[-1],5) for i in Const.dlt1])
            self.treatment_layer = torch.nn.Linear(hidden_layers[-1],len(Const.modifications))
        else:
            #we only have dlt yes or no for the second state?
#             self.dlt_layers = torch.nn.ModuleList([torch.nn.Linear(hidden_layers[-1],2) for i in Const.dlt2])
            self.treatment_layer = torch.nn.Linear(hidden_layers[-1],len(Const.ccs))
   
    def forward(self,xin):
        x = self.normalize(xin)
        x = self.input_dropout(x)
        for layer in self.layers:
            x = layer(x)
#         x = self.batchnorm(x)
        x = self.dropout(x)
        x_pd = self.disease_layer(x)
        x_nd = self.nodal_disease_layer(x)
        x_mod = self.treatment_layer(x)
        x_dlts = [layer(x) for layer in self.dlt_layers]
        
        #If I relu the dlts it breaks idk why
        #the rest needs an activation functiona tha makes everything non-negative so the zeroing out with no IC decisions works properly
        x_pd = self.relu(x_pd)
        x_nd = self.relu(x_nd)
        x_mod = self.relu(x_mod)
        
        #last input is decision 1 or 0, if we have no treatment on first decision we have not transitional outcomes so reflect that
        #this is hardcoded based on original order so check this if I change stuff
        #this only kind of works since I think it can end up all zero and softmaxes to .33% flat
        if self.state == 1:
            #pd and nd, shrink complete and partial response columns if decision is 0
            scale = torch.gt(xin[:,-1],.5).view(-1,1)
            x_pd[:,0:2] = x_pd[:,0:2]*scale
            x_nd[:,0:2]  = x_nd[:,0:2] *scale
            #shrink all but "no modifications"
            x_mod[:,1:]  = x_mod[:,1:] *scale
        x_pd = self.softmax(x_pd)
        x_nd = self.softmax(x_nd)
        x_mod = self.softmax(x_mod)
        #dlts are array of nbatch x n_dlts x predictions
        x_dlts = torch.cat([self.sigmoid(xx) for xx in x_dlts],axis=1)
        #dlts I think are only for chemo so in both ic and cc we do zero if decision is 0
        #this is after sigmoid because the dlts don't use softmax like the other ones
        x_dlts = x_dlts*(xin[:,-1]).view(-1,1)
        xout = [x_pd, x_nd, x_mod, x_dlts]
        
        return xout

class EndpointSimulator(SimulatorBase):
    
    def __init__(self,
                 input_size,
                 hidden_layers = [500],
                 dropout = 0.7,
                 input_dropout=0.1,
                 state = 1,
                ):
        #predicts disease state (sd, pr, cr) for primar and nodal, then dose modications or cc type (depending on state), and [dlt ratings]
        super().__init__(input_size,hidden_layers=hidden_layers,dropout=dropout,input_dropout=input_dropout,state=state)
        
        self.outcome_layer = torch.nn.Linear(hidden_layers[-1],len(Const.outcomes))
      
        
    def forward(self,x):
        x = self.normalize(x)
        x = self.input_dropout(x)
        for layer in self.layers:
            x = layer(x)
#         x = self.batchnorm(x)
        x = self.dropout(x)
        x= self.outcome_layer(x)
        x = self.sigmoid(x)
        return x

class DecisionModel(SimulatorBase):
    
    def __init__(self,
                 baseline_input_size,#number of baseline features used
                 hidden_layers = [100],
                 dropout = 0.5,
                 input_dropout=0.1,
                 state = 1,
                 eps = 0.01,
                 ):
        #input will be all states up until treatment 3
        input_size = baseline_input_size  + 2*len(Const.dlt1) + len(Const.primary_disease_states)  + len(Const.nodal_disease_states)  + len(Const.ccs)  + len(Const.modifications) + 2
            
        super().__init__(input_size,hidden_layers=hidden_layers,dropout=dropout,input_dropout=input_dropout,eps=eps,state='decisions')
        self.final_layer = torch.nn.Linear(hidden_layers[-1],len(Const.decisions)*2)

#         self.final_layer = torch.nn.Linear(hidden_layers[-1],1)
        self.sigmoid = torch.nn.Sigmoid()
        
    def add_position_token(self,x,position):
        #add 2 binary variables for if the state has already passed
        if position == 0:
            token = torch.zeros((x.shape[0],2))
            x = torch.cat([x,token],dim=1)
        if position == 1:
            token1 = torch.ones((x.shape[0],1))
            token2 = torch.zeros((x.shape[0],1))
            x = torch.cat([x,token1,token2],dim=1)
        if position == 2:
            token1 = torch.zeros((x.shape[0],1))
            token2 = torch.ones((x.shape[0],1))
            x = torch.cat([x,token1,token2],dim=1)
        if position == 3:
            token1 = torch.ones((x.shape[0],1))
            token2 = torch.ones((x.shape[0],1))
            x = torch.cat([x,token1,token2],dim=1)
        return x
    
    def get_embedding(self,xbase,xdlt,xpd,xnd,xcc,xmod,position=0):
        xbase = self.normalize(xbase)
        x = torch.cat([xbase,xdlt,xpd,xnd,xcc,xmod],dim=1)
        x = self.add_position_token(x,position)
        for layer in self.layers:
            x = layer(x)
        return x
    
    def forward(self,xbase,xdlt1,xdlt2,xpd,xnd,xcc,xmod,position=0):
        #position is 0-2
#         [xbase, xdlt, xpd, xnd, xcc,xmod] = x
        xbase = self.normalize(xbase)
        x = torch.cat([xbase,xdlt1,xdlt2,xpd,xnd,xcc,xmod],dim=1)
        x = self.input_dropout(x)
        x = self.add_position_token(x,position)
        for layer in self.layers:
            x = layer(x)
        x = self.dropout(x)
        x = self.final_layer(x)
        x = self.sigmoid(x)
        return x

# +
class DecisionAttentionModel(DecisionModel):
    
    def __init__(self,
                 baseline_input_size,#number of baseline features used
                 hidden_layers = [100],
                 attention_heads=[5], 
                 embed_size=100,
                 dropout = 0.5,
                 input_dropout=0.1,
                 state = 1,
                 eps = 0.01,
                 ):
        #input will be all states up until treatment 3
        input_size = baseline_input_size  + 2*len(Const.dlt1) + len(Const.primary_disease_states)  + len(Const.nodal_disease_states)  + len(Const.ccs)  + len(Const.modifications) + 2
        
        self.baseline_input_size= baseline_input_size
        self.input_sizes = {
            'baseline': baseline_input_size,
            'dlt': len(Const.dlt1),
            'pd': len(Const.primary_disease_states),
            'nd': len(Const.nodal_disease_states),
            'cc': len(Const.ccs),
            'modifications': len(Const.modifications),
        }
        
        super().__init__(input_size,hidden_layers=hidden_layers,dropout=dropout,input_dropout=input_dropout,eps=eps,state='decisions')
        
        #to make the input to attention divisible by the initial layer size
        #all layer sizes and embed size need to be divisible by all attention heads
        if embed_size == 0:
            attention_heads[0] = 1
            self.resize_layer = lambda x: x
            curr_size = input_size
        else:
            self.resize_layer = torch.nn.Linear(input_size,embed_size)
            curr_size = embed_size
        #overrite layer intitialization
        layers = []
        attentions = []
        norms = []
        
        i = 0
        for aheads,lindim in zip(attention_heads,hidden_layers):
            attention = torch.nn.MultiheadAttention(curr_size,aheads)
            linear = torch.nn.Linear(curr_size,lindim)
            norm = torch.nn.LayerNorm(curr_size)
            layers.append(linear)
            attentions.append(attention)
            norms.append(norm)
            curr_size = lindim
            
        self.layers = torch.nn.ModuleList(layers)
        self.attentions = torch.nn.ModuleList(attentions)
        self.norms = torch.nn.ModuleList(norms)
        self.final_layer = torch.nn.Linear(hidden_layers[-1],len(Const.decisions)*2)
        self.activation = torch.nn.ReLU()
        self.register_buffer('memory',None)
    
    def get_embedding(self,x,position=0,memory=None,use_saved_memory=False):
        xbase = x[:,0:self.baseline_input_size]
        xx = x[:,self.baseline_input_size:]
        xbase = self.normalize(xbase)
        x = torch.cat([xbase,xx],dim=1)
        x = self.input_dropout(x)
        x = self.add_position_token(x,position)
        x = self.activation(self.resize_layer(x))
        if use_saved_memory:
            memory = self.memory
            #if I use mutliple stages it will be the first axis
            if memory is not None and memory.ndim > 2:
                memory = memory[position]
            if memory is None:
                print('passed saved to decision model but no memory has been saved')
       
        if memory is not None:
            m1 = memory[:,0:self.baseline_input_size]
            m2 = memory[:,self.baseline_input_size:]
            m1 = self.normalize(m1)
            memory = torch.cat([m1,m2],dim=1)
            memory = self.add_position_token(memory,position)
            memory = self.activation(self.resize_layer(memory))
        i = len(self.attentions)
        for attention,layer,norm in zip(self.attentions,self.layers,self.norms):
            if memory is not None:
                x2, attention_weights = attention(x,memory,memory)
                x2 = norm(x2 + x)
            else:
                x2, attention_weights = attention(x,x,x)
                x2 = norm(x2+x)
                i = 0
            x2 = self.activation(x2)
            x = layer(x2)
            x = self.activation(x)
            if i > 1:
                memory2, _ = attention(memory,memory,memory)
                memory = norm(memory2+memory)
                memory = self.activation(memory)
                memory = layer(memory)
                memory = self.activation(memory)
                i -= 1
        return x
    
    def save_memory(self,newmemory):
        self.memory= newmemory
    
    def get_attributions(self,x,output=-1,target=0,**kwargs):
        if output == -1:
            model = lambda x: self.forward(x,**kwargs)
        else:
            model = lambda x: self.forward(x,**kwargs)[output]
        ig = IntegratedGradients(model)
        base = torch.zeros(x.shape)
        if self.memory is not None:
            if self.memory.ndim < 3:
                m = self.memory
            else:
                pos = kwargs.get('position',2)
                m = self.memory[pos]
            base[:] = torch.median(m,dim=0)[0].type(torch.FloatTensor)
        attributions = ig.attribute(x,base,target=target)
        return attributions
    
    def forward(self,x,position=0,memory=None,use_saved_memory=False):
        #position is 0-2
        x = self.get_embedding(x,position=position,memory=memory,use_saved_memory=use_saved_memory)
        x = self.dropout(x)
        x = self.final_layer(x)
        x = self.sigmoid(x)
        return x

#     def forward(self,xbase,xdlt1,xdlt2,xpd,xnd,xcc,xmod,position=0):
#         #position is 0-2
# #         [xbase, xdlt, xpd, xnd, xcc,xmod] = x
#         xbase = self.normalize(xbase)
#         x = torch.cat([xbase,xdlt1,xdlt2,xpd,xnd,xcc,xmod],dim=1)
#         x = self.input_dropout(x)
#         x = self.add_position_token(x,position)
#         x = self.activation(self.resize_layer(x))
#         for attention,layer,norm in zip(self.attentions,self.layers,self.norms):
#             x2, attention_weights = attention(x,x,x)
#             x2 = norm(x2+x)
#             x2 = self.activation(x2)
#             x = layer(x2)
#             x = self.activation(x)
#         x = self.dropout(x)
#         x = self.final_layer(x)
#         x = self.sigmoid(x)
#         return x


# -

class OutcomeAttentionSimulator(SimulatorAttentionBase):
    
    def __init__(self,
                 input_size,
                 hidden_layers = [500],
                 attention_heads=[4], 
                 embed_size=100,
                 dropout = 0.5,
                 input_dropout=0.1,
                 state = 1,
                ):
        #predicts disease state (sd, pr, cr) for primar and nodal, then dose modications or cc type (depending on state), and [dlt ratings]
        super().__init__(input_size,hidden_layers=hidden_layers,dropout=dropout,input_dropout=input_dropout,state=state)
        
        self.disease_layer = torch.nn.Linear(hidden_layers[-1],len(Const.primary_disease_states))
        self.nodal_disease_layer = torch.nn.Linear(hidden_layers[-1],len(Const.nodal_disease_states))
        #dlt ratings are 0-4 even though they don't always appear
        
        self.dlt_layers = torch.nn.ModuleList([torch.nn.Linear(hidden_layers[-1],1) for i in Const.dlt1])
        assert( state in [1,2])
        if state == 1:
#             self.dlt_layers = torch.nn.ModuleList([torch.nn.Linear(hidden_layers[-1],5) for i in Const.dlt1])
            self.treatment_layer = torch.nn.Linear(hidden_layers[-1],len(Const.modifications))
        else:
            #we only have dlt yes or no for the second state?
#             self.dlt_layers = torch.nn.ModuleList([torch.nn.Linear(hidden_layers[-1],2) for i in Const.dlt2])
            self.treatment_layer = torch.nn.Linear(hidden_layers[-1],len(Const.ccs))

        
    def forward(self,x):
        x = self.normalize(x)
        x = self.input_dropout(x)
        x = self.activation(self.resize_layer(x))
        for attention,layer,norm in zip(self.attentions,self.layers,self.norms):
            x2, attention_weights = attention(x,x,x)
            x2 = norm(x2+x)
            x2 = self.activation(x2)
            x = layer(x2)
            x = self.activation(x)
        x = self.dropout(x)
        x_pd = self.disease_layer(x)
        x_nd = self.nodal_disease_layer(x)
        x_mod = self.treatment_layer(x)
        x_dlts = [layer(x) for layer in self.dlt_layers]
        
        x_pd = self.softmax(x_pd)
        x_nd = self.softmax(x_nd)
        x_mod = self.softmax(x_mod)
        #dlts are array of nbatch x n_dlts x predictions
        x_dlts = torch.cat([self.sigmoid(xx) for xx in x_dlts],axis=1)
        return [x_pd, x_nd, x_mod, x_dlts]

class EndpointAttentionSimulator(SimulatorAttentionBase):
    
    def __init__(self,
                 input_size,
                 hidden_layers = [500],
                 attention_heads=[2],
                 dropout = 0.7,
                 input_dropout=0.1,
                 embed_size=500,
                 state = 1,
                ):
        #predicts disease state (sd, pr, cr) for primar and nodal, then dose modications or cc type (depending on state), and [dlt ratings]
                    
        super().__init__(input_size,
                         hidden_layers=hidden_layers,
                         attention_heads=attention_heads,
                         dropout=dropout,input_dropout=input_dropout,
                         embed_size=embed_size,
                         state=state)
        
        self.outcome_layer = torch.nn.Linear(hidden_layers[-1],len(Const.outcomes))
      
        
    def forward(self,x):
        x = self.normalize(x)
        x = self.input_dropout(x)
        x = self.activation(self.resize_layer(x))
        for attention,layer,norm in zip(self.attentions,self.layers,self.norms):
            x2, attention_weights = attention(x,x,x)
            x2 = norm(x2+x)
            x2 = self.activation(x2)
            x = layer(x2)
            x = self.activation(x)
        x = self.dropout(x)
        x= self.outcome_layer(x)
        x = self.sigmoid(x)
        return x

def df_to_torch(df,ttype  = torch.FloatTensor):
    values = df.values.astype(float)
    values = torch.from_numpy(values)
    return values.type(ttype)
