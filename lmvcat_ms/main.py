import os
os.environ['CUDA_VISIBLE_DEVICES'] = '0'
import os.path as osp
import utils
from utils import AverageMeter
import MLdataset
import argparse
import time
from model import get_model
import evaluation
# import torch
import numpy as np
from myloss import Loss

import mindspore as ms
from mindspore import nn, Tensor
import mindspore.numpy as mnp

import mindspore.context as context
context.set_context(device_target="GPU")
import copy
def train(loader, model, loss_model, optimizer, sche, epoch,logger):

    batch_time = AverageMeter()
    data_time = AverageMeter()
    losses = AverageMeter()
    model.set_train()
    end = time.time()

    individual_all_z = []
    for i, (data0,data1,data2,data3,data4,data5, label, inc_V_ind, inc_L_ind) in enumerate(loader):
        data = [data0,data1,data2,data3,data4,data5]
        data_time.update(time.time() - end)
        # data=[v_data for v_data in data]
        label = label

        inc_V_ind = inc_V_ind.float()
        inc_L_ind = inc_L_ind.float()
        def forward_fn(data,inc_V_ind):

            pred0,pred1,x_tran,mv_labels = model(data,mask=inc_V_ind)
            cls_loss2=0
            cls_loss = loss_model.weighted_BCE_loss(pred0,label,inc_L_ind)
            cls_loss2 = loss_model.weighted_BCE_loss(pred1,label,inc_L_ind)
            if args.alpha>0:
                cont_loss2 = loss_model.contrastive_loss2(x_tran,label,inc_V_ind, inc_L_ind)
            else:
                cont_loss2 = 0
            # loss = cls_loss
            loss = cls_loss + args.alpha * cont_loss2+ cls_loss2*args.beta 
            # loss = cls_loss + args.alpha * cont_loss2
            return loss,None
        grad_fn = ms.value_and_grad(forward_fn, None, optimizer.parameters, has_aux=True)

        (loss,_), grads = grad_fn(data,inc_V_ind)
        # grads = ms.ops.depend(grads, optimizer(grads))
        optimizer(grads)
        # loss = loss_CL + args.gamma * loss_AE  + loss_WCont * args.beta 


        
        # opt.step()
        # print(model.classifier.parameters().grad)
        losses.update(loss.numpy())
        batch_time.update(time.time()- end)
        end = time.time()

    logger.info('Epoch:[{0}]\t'
                  'Time {batch_time.avg:.3f}\t'
                  'Data {data_time.avg:.3f}\t'
                  'Loss {losses.avg:.3f}'.format(
                        epoch,   batch_time=batch_time,
                        data_time=data_time, losses=losses))
    # print("all0",all0)
    return losses,model

def test(loader, model, loss_model, epoch,logger):
    batch_time = AverageMeter()
    losses = AverageMeter()
    total_labels = []
    total_preds = []
    model.set_train(False)
    end = time.time()
    for i, (data0,data1,data2,data3,data4,data5, label, inc_V_ind, inc_L_ind) in enumerate(loader):
        data = [data0,data1,data2,data3,data4,data5]
        # data_time.update(time.time() - end)
        data=[v_data for v_data in data]
        inc_V_ind = inc_V_ind.float()
        pred0,pred1, _, _ = model(data,mask=inc_V_ind)
        pred = pred0
        total_labels = np.concatenate((total_labels,label.numpy()),axis=0) if len(total_labels)>0 else label.numpy()
        total_preds = np.concatenate((total_preds,pred.numpy()),axis=0) if len(total_preds)>0 else pred.numpy()
        
        batch_time.update(time.time()- end)
        end = time.time()
    total_labels=np.array(total_labels)
    total_preds=np.array(total_preds)

    evaluation_results=evaluation.do_metric(total_preds,total_labels)
    logger.info('Epoch:[{0}]\t'
                  'Time {batch_time.avg:.3f}\t'
                  'AP {ap:.3f}\t'
                  'HL {hl:.3f}\t'
                  'RL {rl:.3f}\t'
                  'AUC {auc:.3f}\t'.format(
                        epoch,   batch_time=batch_time,
                        ap=evaluation_results[0], 
                        hl=evaluation_results[1],
                        rl=evaluation_results[2],
                        auc=evaluation_results[3]
                        ))
    return evaluation_results


def main(args,file_path):
    data_path = osp.join(args.root_dir, args.dataset, args.dataset+'_six_view.mat')
    fold_data_path = osp.join(args.root_dir, args.dataset, args.dataset+'_six_view_MaskRatios_' + str(
                                args.mask_view_ratio) + '_LabelMaskRatio_' +
                                str(args.mask_label_ratio) + '_TraindataRatio_' + 
                                str(args.training_sample_ratio) + '.mat')
    
    folds_num = args.folds_num
    folds_results = [AverageMeter() for i in range(9)]
    if args.logs:
        logfile = osp.join(args.logs_dir,args.name+args.dataset+'_V_' + str(
                                    args.mask_view_ratio) + '_L_' +
                                    str(args.mask_label_ratio) + '_T_' + 
                                    str(args.training_sample_ratio) + '_'+str(args.alpha)+'_'+str(args.beta)+'.txt')
    else:
        logfile=None
    logger = utils.setLogger(logfile)
    # device = torch.device('cuda:0')
    for fold_idx in range(folds_num):
        fold_idx=fold_idx
        train_dataloder,train_dataset = MLdataset.getIncDataloader(data_path, fold_data_path,training_ratio=args.training_sample_ratio,fold_idx=fold_idx,mode='train',batch_size=args.batch_size,shuffle = False,num_workers=1)
        test_dataloder,test_dataset = MLdataset.getIncDataloader(data_path, fold_data_path,training_ratio=args.training_sample_ratio,val_ratio=0.15,fold_idx=fold_idx,mode='test',batch_size=args.batch_size,num_workers=1)
        val_dataloder,val_dataset = MLdataset.getIncDataloader(data_path, fold_data_path,training_ratio=args.training_sample_ratio,fold_idx=fold_idx,mode='val',batch_size=args.batch_size,num_workers=1)
        d_list = train_dataset.d_list
        classes_num = train_dataset.classes_num
        
        
        model = get_model(len(d_list),d_list,d_model=512,n_layers=1,heads=4,classes_num=train_dataset.classes_num,dropout=0.3,exponent=args.gamma)
        # print(model)
        loss_model = Loss()
        # crit = nn.BCELoss()

        optimizer = nn.SGD(model.trainable_params(), learning_rate=args.lr, momentum=0.9)
        # optimizer = nn.Adam(model.trainable_params(), learning_rate=args.lr)
        # scheduler = StepLR(optimizer, step_size=5, gamma=0.85)
        # scheduler = CosineAnnealingWarmRestarts(optimizer, T_0=4, T_mult=2)
        scheduler = None
        
        
        logger.info('train_data_num:'+str(len(train_dataset))+'  test_data_num:'+str(len(test_dataset))+'   fold_idx:'+str(fold_idx))
        print(args)
        static_res = 0
        epoch_results = [AverageMeter() for i in range(9)]
        total_losses = AverageMeter()
        train_losses_last = AverageMeter()
        best_epoch=0
        best_model_dict = {'model':model.parameters_dict(),'epoch':0}
        for epoch in range(args.epochs):
            
            train_losses,model = train(train_dataloder,model,loss_model,optimizer,scheduler,epoch,logger)
            # test_results = test(test_dataloder,model,loss_model,epoch,logger)
            val_results = test(val_dataloder,model,loss_model,epoch,logger)

            
            if val_results[0]*0.5+val_results[2]*0.25+val_results[3]*0.5>=static_res:
                static_res = val_results[0]*0.5+val_results[2]*0.25+val_results[3]*0.5
                best_model_dict['model'] = copy.deepcopy(model.parameters_dict())
                best_model_dict['epoch'] = epoch
                best_epoch=epoch
            train_losses_last = train_losses
            total_losses.update(train_losses.sum)
        ms.load_param_into_net(model,best_model_dict['model'])
        test_results = test(test_dataloder,model,loss_model,epoch,logger)

        logger.info('final: fold_idx:{} best_epoch:{}\t best:ap:{:.4}\t HL:{:.4}\t RL:{:.4}\t AUC_me:{:.4}\n'.format(fold_idx,best_epoch,test_results[0],test_results[1],
            test_results[2],test_results[3]))

        for i in range(9):
            folds_results[i].update(test_results[i])
        if args.save_curve:
            np.save(osp.join(args.curve_dir,args.dataset+'_V_'+str(args.mask_view_ratio)+'_L_'+str(args.mask_label_ratio))+'_'+str(fold_idx)+'.npy', np.array(list(zip(epoch_results[0].vals,train_losses.vals))))
    file_handle = open(file_path, mode='a')
    if os.path.getsize(file_path) == 0:
        file_handle.write(
            'AP HL RL AUCme one_error coverage macAUC macro_f1 micro_f1 lr alpha beta gamma\n')
    # generate string-result of 9 metrics and two parameters
    res_list = [str(round(res.avg,4))+'+'+str(round(res.std,4)) for res in folds_results]
    res_list.extend([str(args.lr),str(args.alpha),str(args.beta),str(args.gamma)])
    res_str = ' '.join(res_list)
    file_handle.write(res_str)
    file_handle.write('\n')
    file_handle.close()
        

def filterparam(file_path,index):
    params = []
    if os.path.exists(file_path):
        file_handle = open(file_path, mode='r')
        lines = file_handle.readlines()
        lines = lines[1:] if len(lines)>1 else []
        params = [[float(line.split(' ')[idx]) for idx in index] for line in lines ]
    return params

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    # misc
    working_dir = osp.dirname(osp.abspath(__file__)) 
    parser.add_argument('--logs-dir', type=str, metavar='PATH', 
                        default=osp.join(working_dir, 'logs'))
    parser.add_argument('--logs', default=False, type=bool)
    parser.add_argument('--records-dir', type=str, metavar='PATH', 
                        default=osp.join(working_dir, 'records'))
    parser.add_argument('--file-path', type=str, metavar='PATH', 
                        default='')
    parser.add_argument('--root-dir', type=str, metavar='PATH', 
                        default='data/')
    parser.add_argument('--dataset', type=str, default='')#mirflickr corel5k pascal07 iaprtc12 espgame
    parser.add_argument('--datasets', type=list, default=['pascal07'])
    parser.add_argument('--mask-view-ratio', type=float, default=0.5)
    parser.add_argument('--mask-label-ratio', type=float, default=0.5)
    parser.add_argument('--training-sample-ratio', type=float, default=0.7)
    parser.add_argument('--folds-num', default=1, type=int)
    parser.add_argument('--weights-dir', type=str, metavar='PATH', 
                        default=osp.join(working_dir, 'weights'))
    parser.add_argument('--curve-dir', type=str, metavar='PATH', 
                        default=osp.join(working_dir, 'curves'))
    parser.add_argument('--save-curve', default=False, type=bool)
    parser.add_argument('--seed', default=1, type=int)
    parser.add_argument('--workers', default=8, type=int)
    
    parser.add_argument('--name', type=str, default='1_final_')
    # Optimization args
    parser.add_argument('--lr', type=float, default=1e-1)
    parser.add_argument('--momentum', type=float, default=0.9)
    parser.add_argument('--weight_decay', type=float, default=1e-4)
    parser.add_argument('--epochs', type=int, default=10) #10 for pascal07 20 for mirflickr and 200 for others
    
    # Training args
    parser.add_argument('--batch_size', type=int, default=128)
    parser.add_argument('--alpha', type=float, default=1e-1)
    parser.add_argument('--beta', type=float, default=1e-1)
    parser.add_argument('--gamma', type=float, default=1e-1)


    
    args = parser.parse_args()
    
    if args.logs:
        if not os.path.exists(args.logs_dir):
            os.makedirs(args.logs_dir)
    if args.save_curve:
        if not os.path.exists(args.curve_dir):
            os.makedirs(args.curve_dir)
    if True:
        if not os.path.exists(args.records_dir):
            os.makedirs(args.records_dir)
    lr_list = [1e-2]
    alpha_list = [1e1] #1e1 
    beta_list = [1e-1]  #1e-1
    gamma_list = [2]

    
    for lr in lr_list:
        args.lr = lr
        if args.lr >= 0.01:
            args.momentumkl = 0.90
        for alpha in alpha_list:
            args.alpha = alpha
            for beta in beta_list:
                args.beta = beta
                for gamma in gamma_list:
                    args.gamma = gamma
                    for dataset in args.datasets:
                        args.dataset = dataset
                        file_path = osp.join(args.records_dir,args.name+args.dataset+'_ViewMask_' + str(
                                        args.mask_view_ratio) + '_LabelMask_' +
                                        str(args.mask_label_ratio) + '_Training_' + 
                                        str(args.training_sample_ratio) + '_bs128.txt')
                        args.file_path = file_path
                        existed_params = filterparam(file_path,[-3,-2,-1])
                        if [args.alpha,args.beta,args.gamma] in existed_params:
                            print('existed param! alpha:{} beta:{} gamma:{} '.format(args.alpha,args.beta,args.gamma))
                            # continue
                        main(args,file_path)