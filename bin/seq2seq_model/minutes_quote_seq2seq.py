'''
seq2seq models：
    利用seq2seq模型，训练新闻序列模型预测股价序列
    相当于是 用新闻编码的seq预测股价的seq 
参数：
    config：
        data:数据集预处理配置
        model:定义模型构造
        training:定义训练的迭代方式
    定义股价波动标签：
        默认以交易日 freq="day" 为数据窗口切分数据，以其他窗口频率切分数据计算方法相同
        定义数据窗口指标：今日开盘价open、涨跌幅up_down、波动幅fluctuation、成交量总额volume_all
        y = [date, open, up_down, fluctuation, volume_all]
        open = 今日开盘价
        up_down = 100 * (今日收盘价 - 今日开盘价)/今日开盘价
        fluctuation = 100 * (今日最高价 - 今日最低价)/今日开盘价
        volume_all = sum(今日分钟成交量)/10000
        由于价格是个连续数据，不便于定义标签，所以我们用涨跌幅、波动幅度定义标签
        计算方法为：
        tag = 
        [log(up_down+1)]  (向下取整)  up_down >= 1  取值范围：1、2、3...
        [log(up_down+1)]  (向下取1/8的1、2、4倍)  0<= up_down < 1  取值范围：0、1/8、1/4、1/2
        -[log(-up_down+1)]  (向下取1/8的1、2、4倍)  -1<= up_down < 0  取值范围：-1/8、-1/4、-1/2
        -[log(-up_down+1)]  (向下取整)  up_down < -1  取值范围：-1、-2、-3...
        tag计算方法适用于涨跌幅和波动幅，最终标签是一个二维向量，分别为涨跌幅tag和波动幅tag
    数据集：
        数据集为六个月的分钟交易数据，每日的股价标签为240行。
        特征集为六个月的财经新闻，并经过bert编码之后，平均每日500-700条新闻，时间不连续。
'''
import datetime as dt
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from keras import backend as K
from keras.callbacks import EarlyStopping, ModelCheckpoint
from keras.layers import (Activation, Bidirectional, Dense, Dropout, Input,
                          Lambda, TimeDistributed, add, concatenate, multiply)
from keras.models import Model, Sequential
from keras.preprocessing.sequence import TimeseriesGenerator
from matplotlib import pyplot as plt
from sklearn.preprocessing import StandardScaler

import recurrentshop
import seq2seq
from recurrentshop import LSTMCell, RecurrentSequential
from recurrentshop.cells import *
from .cells import LSTMDecoderCell, AttentionDecoderCell

sys.path.append('C:\\Users\\longf.DESKTOP-7QSFE46\\GitHub\\A-Stock-Prediction-System-with-GAN-and-DRL')


def Seq2Seq(output_dim, output_length, batch_input_shape=None,
            input_shape=None, batch_size=None, input_dim=None, input_length=None,
            hidden_dim=None, depth=1, broadcast_state=True, unroll=False,
            stateful=False, inner_broadcast_state=True, teacher_force=False,
            peek=False, dropout=0.):

    '''
    Seq2seq model based on [1] and [2].
    This model has the ability to transfer the encoder hidden state to the decoder's
    hidden state(specified by the broadcast_state argument). Also, in deep models
    (depth > 1), the hidden state is propogated throughout the LSTM stack(specified by
    the inner_broadcast_state argument. You can switch between [1] based model and [2]
    based model using the peek argument.(peek = True for [2], peek = False for [1]).
    When peek = True, the decoder gets a 'peek' at the context vector at every timestep.

    [1] based model:

            Encoder:
            X = Input sequence
            C = LSTM(X); The context vector

            Decoder:
    y(t) = LSTM(s(t-1), y(t-1)); Where s is the hidden state of the LSTM (h and c)
    y(0) = LSTM(s0, C); C is the context vector from the encoder.

    [2] based model:

            Encoder:
            X = Input sequence
            C = LSTM(X); The context vector

            Decoder:
    y(t) = LSTM(s(t-1), y(t-1), C)
    y(0) = LSTM(s0, C, C)
    Where s is the hidden state of the LSTM (h and c), and C is the context vector
    from the encoder.

    Arguments:

    output_dim : Required output dimension.
    hidden_dim : The dimension of the internal representations of the model.
    output_length : Length of the required output sequence.
    depth : Used to create a deep Seq2seq model. For example, if depth = 3,
                    there will be 3 LSTMs on the enoding side and 3 LSTMs on the
                    decoding side. You can also specify depth as a tuple. For example,
                    if depth = (4, 5), 4 LSTMs will be added to the encoding side and
                    5 LSTMs will be added to the decoding side.
    broadcast_state : Specifies whether the hidden state from encoder should be
                                      transfered to the deocder.
    inner_broadcast_state : Specifies whether hidden states should be propogated
                                                    throughout the LSTM stack in deep models.
    peek : Specifies if the decoder should be able to peek at the context vector
               at every timestep.
    dropout : Dropout probability in between layers.


    '''

    if isinstance(depth, int):
        depth = (depth, depth) 
        # depth是整数时，相当于编码器和解码器都有相同的层数
    if batch_input_shape:
        shape = batch_input_shape 
        # 批输入的shape作为模型输入的shape
    elif input_shape:
        shape = (batch_size,) + input_shape 
        # 不指定batch input shape，则用批大小拼接input shape，如batch size为32，input为768，拼接后就是（32，768）
        # input shape 必须是一个元组
    elif input_dim:
        if input_length:
            shape = (batch_size,) + (input_length,) + (input_dim,)
            # 一般情况下通用的shape（批大小，输入序列长度，输入维度）
        else:
            shape = (batch_size,) + (None,) + (input_dim,)
    else:
        # TODO Proper error message
        raise TypeError
    if hidden_dim is None:
        hidden_dim = output_dim
        # 隐藏层的维度如果也是None？那代表什么呢

    encoder = RecurrentSequential(readout=True, state_sync=inner_broadcast_state,
                                  unroll=unroll, stateful=stateful,
                                  return_states=broadcast_state)
    '''
    参数：
        readout：是否额外将输出进行处理 选项有add（True），multiply，average，maximum等
        state_sync：状态是否在内部传播，源码中对initial_states的处理不一样，对每个cell的state都进行传播到下一个batch
        stateful：keras特性，在不同的batch之间传递cells的状态，而不是仅仅在cell之间传递状态，即stateful
                  在stateful = True 时，我们要在fit中手动使得shuffle = False。随后，在X[i]（表示输入矩阵中第
                  i个sample）这个小序列训练完之后，Keras会将将训练完的记忆参数传递给X[i+bs]（表示第i+bs个sample）,
                  作为其初始的记忆参数。
        unroll：keras特性，将LSTM网络展开，也就是原本的时序序列直接展开成多个cell拼接，可以加快速度，但是占用更多内存
    '''
    for _ in range(depth[0]):
        encoder.add(LSTMCell(hidden_dim, batch_input_shape=(shape[0], hidden_dim)))
        encoder.add(Dropout(dropout))
    # 根据depth[0]指定编码器深度

    dense1 = TimeDistributed(Dense(hidden_dim))
    '''
    # dence1：
    # 使用TimeDistributed层对1个batch中样本（input_length，input_dim）每个向量都进行Dense操作，在整个length长度下，这个样本
    # 都共享TimeDistributed层的权重，即输出后变成（batch_size，input_length，hidden_dim）
    '''
    dense1.supports_masking = True
    dense2 = Dense(output_dim)
    '''
    dence2:
        处理从encoder之后的编码，整型为output_dim，再送给decoder
    '''

    decoder = RecurrentSequential(readout='add' if peek else 'readout_only',
                                  state_sync=inner_broadcast_state, decode=True,
                                  output_length=output_length, unroll=unroll,
                                  stateful=stateful, teacher_force=teacher_force)
    '''
    参数：
        teaching force ：它每次不使用上一个state的输出作为下一个state的输入，而是直接
        使用训练数据的标准答案(ground truth)的对应上一项作为下一个state的输入。
        结合beam search和计划抽样，使用一个概率p来决定使用teaching还是free training，随着训练epoch增加，
        概率p也会减少，相当于逐步的减小teaching的采样频率，确保模型既能快速学习，又有泛化能力
    '''

    for _ in range(depth[1]):
        decoder.add(Dropout(dropout, batch_input_shape=(shape[0], output_dim)))
        decoder.add(LSTMDecoderCell(output_dim=output_dim, hidden_dim=hidden_dim,
                                    batch_input_shape=(shape[0], output_dim)))
    # 根据depth[1]指定解码器的深度

    _input = Input(batch_shape=shape)
    _input._keras_history[0].supports_masking = True
    encoded_seq = dense1(_input)
    # 对输入数据先通过TimeDistributed层，处理成hidden_dim的向量维度
    encoded_seq = encoder(encoded_seq)
    # 再通过encoder编码
    # 以下是一些选项的处理，是否广播状态，是否teaching模式等
    if broadcast_state:
        assert type(encoded_seq) is list
        states = encoded_seq[-2:]
        encoded_seq = encoded_seq[0]
    else:
        states = None
    encoded_seq = dense2(encoded_seq)
    inputs = [_input]
    if teacher_force:
        truth_tensor = Input(batch_shape=(shape[0], output_length, output_dim))
        truth_tensor._keras_history[0].supports_masking = True
        inputs += [truth_tensor]
    # 编码之后的后续处理

    # 解码，initial_state是否接受从编码器传递过来的状态，
    decoded_seq = decoder(encoded_seq,
                          ground_truth=inputs[1] if teacher_force else None,
                          initial_readout=encoded_seq, initial_state=states)
    
    seq2seq_model = Model(inputs, decoded_seq)
    # 整个模型就是从输入到解码seq，可以将编码器单独拿出来，使用其中的编码
    # 另外，模型处理的实时新闻序列到股价波动序列，如果要将休盘期内新闻信息也纳入训练，
    # 则需要共享编码器和解码权重，并增加新的Flatten和Dence层，将解码器输出序列视为波动编码，再进入Dence输出标量
    # 涉及到，在RecurrentSequential后增加Sequencial序列
    seq2seq_model.encoder = encoder
    seq2seq_model.decoder = decoder

    decoded_vec = Flatten(decoded_seq)
    decoded_vec = Dense(1, activation='tanh')
    seq2vec_model = Model(inputs, [decoded_seq, decoded_vec])
    # 最终模型有1个输入，2个输出

    return seq2vec_model


class Timer():
    def __init__(self):
        self.start_dt = None

    def start(self):
        self.start_dt = dt.datetime.now()

    def stop(self):
        end_dt = dt.datetime.now()
        print('Time taken: %s' % (end_dt - self.start_dt))


class DataLoader():
    """
    参数：
        config:配置
            "x_filename": 新闻bert编码文件路径
            "x_sequence_length": 编码序列长度
            "x_feature_dim":编码的维度
            "x_fill_blank":用什么随机数方式填补无编码时刻数据，默认用标准正态分布 normal
            "y_filename": 股价数据文件路径
            "y_columns": 股价数据的列
            "y_predict_cols":用于预测的列
            "train_test_split": 训练集和测试集分割比例
            "predict_window": 序列中预测窗口的比例
            "normalise": 标准化方式，全局还是窗口内
            "window_slice_freq": 切分窗口的频率，day还是hour
        nrows：
            数据读入的行数
    方法：
        init:初始化时加载数据，并指定数据行数，避免全部加载占用太多内存
        get_iterator_x_data: 用迭代器载入x数据，避免占用太大内存，
        make_index:处理x，y的索引，使训练x，y对其
        get_delta_y:获取股价的时间变化情况 delta = y(t) - t(t-1)

        split_train_test_data: 切分训练、测试数据
        normalise_data：对数据进行标准化

        generate_x_y_data：用生成器产生训练x，y数据
        fill_blank_x_data:指定随机数函数，填充没有新闻数据的时刻
    """
    def __init__(self, config, nrows=3000):
        '''
        config:
            配置文件
        nrows:
            读入数据的行数
        '''
        # 配置文件分为三部分：数据、模型结构、训练
        self.data_config = config['data']
        # 初始化时读入股价数据，时间序列作为索引，股价数据升序排列，全部读取
        y_data_csv = pd.read_csv(
                                self.data_config['y_filename'], 
                                usecols=self.data_config['y_columns'])
        self.y_data = y_data_csv.set_index(
                        pd.to_datetime(y_data_csv[self.data_config['y_index']])).drop(
                            columns=[x for x in y_data_csv.columns if x.startswith('Unnamed: ')]).sort_index(ascending=True)
        
        # 编码数据，使用时间序列作为索引，部分读入，防止内存溢出，此处注意，由于存储时是按照日期倒叙存储，所以时间序列是倒叙
        # 先读入的日期是最近的日期，处理时要注意
        x_data_csv = pd.read_csv(self.data_config['x_filename'], nrows=nrows)
        self.x_data = x_data_csv.set_index(
            pd.to_datetime(x_data_csv[self.data_config['x_index']])).drop(
                columns=[x for x in x_data_csv.columns if x.startswith('Unnamed: ')]).sort_index(ascending=True)

        self.index_limit = [max(self.x_data.index[0], self.y_data.index[0]), 
                                min(self.x_data.index[-1], self.y_data.index[-1])]
        # 转化为整minute的分钟索引
        self.index_limit[0] = pd.datetime(self.index_limit[0].year, 
                self.index_limit[0].month,
                self.index_limit[0].day,
                self.index_limit[0].hour,
                self.index_limit[0].minute)
        self.index_limit[1] = pd.datetime(self.index_limit[1].year, 
                self.index_limit[1].month,
                self.index_limit[1].day,
                self.index_limit[1].hour,
                self.index_limit[1].minute)

    def split_open_close_data(self):
        '''
        分割开市、休市数据:
            处理x和y的索引，由于x数据集比较大（>1GB），而股价数据集y相对较小（<10MB），
            所以我们首先读入y数据集，获取y的索引，并按照交易日切片，
            股价有效数据的时间段为(9:30-11:30 13:00-15:00)。
            xi作为财经新闻，应该影响其新闻发生之后的股价走势（即投资者获取消息，然后做出投资决策这个前提条件），
            所以，以y索引为主索引，t交易日的y数据为240分钟股价，
            而t交易日的x数据为t-1日15:00之后到t日15:00时之前的24小时数据。
            形成一个1440分钟数据压缩到240分钟股价的映射。
            其中，影响比较直接的是正在交易的240分钟新闻数据，休盘期的信息主要影响下一个开盘时刻的股价。

        返回：
            开市x,y，休市x,y
        '''
        import datetime as dt

        open_time = dt.time(9,30)
        close_time = dt.time(15,0)
        moon_break_time = dt.time(11,30)
        moon_open_time = dt.time(13,1)

        x_open_market_index = [x for x in self.x_data.index 
                                if (x.time() >= open_time and x.time() <= moon_break_time) 
                                or (x.time() >= moon_open_time and x.time() <= close_time)
                              ]
        y_open_market_index = [x for x in self.y_data.index 
                                if (x.time() >= open_time and x.time() <= moon_break_time) 
                                or (x.time() >= moon_open_time and x.time() <= close_time)
                              ]
        x_close_market_index = [x for x in self.x_data.index if x not in x_open_market_index ]
        y_close_market_index = [x for x in self.y_data.index if x not in y_open_market_index ]
        # 这里埋了一个坑，开市包括分割点，但是休市么有分割点，也就是实际的开市交易数据是4*60+1=241分钟的，
        # 不过这样会方便做差计算，因为delta(y) = y(t) - y(t-1)就会只有240分钟数据 。
        y_fluctuation = self.y_data - self.y_data.shift(1)

        return self.x_data[x_open_market_index], y_fluctuation[y_open_market_index], self.x_data[x_close_market_index], y_fluctuation[y_close_market_index]


    def get_iterator_x_data(self, nrows=3000):
        '''
        获取x迭代器：
            使用迭代器获取数据x，避免内存溢出
        '''
        x_data_csv_iter = pd.read_csv(self.data_config['x_filename'], iterator=True)
        # 每次获取nrows
        try:
            while True:
                x_data_iter = x_data_csv_iter.get_chunk(nrows)
                x_data = x_data_iter.set_index(
                    pd.to_datetime(x_data_iter[self.data_config['x_index']])).drop(
                        columns=[x for x in x_data_iter.columns if x.startswith('Unnamed: ')]).sort_index(ascending=True)
                yield x_data
        except Exception as e:
            print(e)


    def split_train_test_data(self, data):
        # 训练集和测试集切分
        i_split = int(len(data) * self.data_config['train_test_split'])
        # 定义标签的列和特征的列 
        '''
        self.data_train = data.get(self.feature_cols).values[:i_split]
        self.data_test  = data.get(self.feature_cols).values[i_split:]
        self.y_train = data.get(self.y_tag).values[:i_split]
        self.y_test = data.get(self.y_tag).values[i_split:]
        self.len_train = len(self.data_train)
        self.len_test = len(self.data_test)
        '''


    def normalise_data(self, data=None):
        '''
        对数据特征进行标准化，参考配置“config.data.normalise”
        '''
        if self.data_config['normalise'] == 'global': # 全局标准化
            normalised_data = self.x_data
        elif self.data_config['normalise'] == 'window': # 对传入的窗口数据标准化
            normalised_data = pd.DataFrame(data)
        scalar = StandardScaler()
        scalar.fit(normalised_data)
        normalised_data = scalar.transform(normalised_data)
        return normalised_data


    def generate_x_y_data(self, x_open, y_open, x_close, y_close):
        '''
        生成x,y序列数据：
            x是编码数据，t日的编码应该是从t-1日15时休市之后起算
            y是股价，对应于股价的变化，即delta(yt) = y(t) - y(t-1)
            y_tag是一个时间段的标签，用一个7元组表示，代码头部已经给出定义
        '''
        y_daily_index = self.y_data.index.to_period('D').unique()
        if x_data[x_data.index==y_daily_index[0]]:
            pass
        # 用股价数据的索引来生成数据
        for idx in daily_index:
            daily_data_x = self.x_data[self.x_data.index.to_period('D') == idx]
            daily_data_y = self.y_data[self.y_data.index.to_period('D') == idx]
            y_open = daily_data_y['open'][0]
            y_close = daily_data_y['close'][-1]
            y_high = daily_data_y['high'].max()
            y_low = daily_data_y['low'].min()
            y_volume = daily_data_y['vol'].sum()
            # 定义每日的标签：均值、涨跌幅度、震荡幅度
            y_i = [daily_data_y['trade_time'][0], daily_data_y['trade_time'][-1], y_open, y_close, y_high, y_low, y_volume]
            yield [daily_data_x, daily_data_y, y_i]

    def _fill_blank_x_data(self, dim=768, distribution="normal", distribution_args=[0, 1]):
        '''
        使用随机数发生器产生一个dim维的随机数向量
        dim:
            向量维度
        distribution:  
            随机数分布
        distribution_args:
            分布的参数
        '''
        seed_from_time = dt.datetime.now().microsecond%10000
        # 从当前时间获取随机数种子
        np.random.seed(seed_from_time)
        if distribution == 'normal': # 正态分布，默认参数（0，1）
            code = np.random.normal((distribution_args[0], distribution_args[1], (dim,)))
        if distribution == 'uniform': # 均匀分布，默认参数（-1，1）
            code = np.random.uniform((distribution_args[0], distribution_args[1], (dim,)))
        return code


class Seq2Seq_Model():
    '''
    seq2seq:
        利用seq2seq模型，对编码的新闻序列建模并
        训练出预测分钟涨跌股价模型
    '''
    def __init__(self, config):
        self.model_config = config['model']
        self.training_config = config['training']

    def load_model(self, filepath):
        print('[Model] Loading model from file %s' % filepath)
        # self.model = load_model(filepath)

    def build_model(self):
        timer = Timer()
        timer.start()

        hps = self.model_config['hyperparameters']
        self.model = Seq2Seq(output_dim=hps['output_dim'], 
                             output_length=hps['output_length'], 
                             batch_input_shape=hps['batch_input_shape'],
                             input_shape=hps['input_shape'], 
                             batch_size=hps['batch_size'], 
                             input_dim=hps['input_dim'], 
                             input_length=hps['input_length'],
                             hidden_dim=hps['hidden_dim'], 
                             depth=hps['depth'], 
                             broadcast_state=hps['broadcast_state'], 
                             unroll=hps['unroll'],
                             stateful=hps['stateful'], 
                             inner_broadcast_state=hps['inner_broadcast_state'], 
                             teacher_force=hps['teacher_force'],
                             peek=hps['peek'], 
                             dropout=hps['dropout'])
        self.model.compile(loss=self.model_config['loss'], optimizer=self.model_config['optimizer'])
        print(self.model.summary())
        print('[Model] Model Compiled')
        timer.stop()

    def train(self, x, y, epochs, batch_size, validation_split, save_dir):
        timer = Timer()
        timer.start()
        print('[Model] Training Started')
        print('[Model] %s epochs, %s batch size' % (epochs, batch_size))
        
        save_fname = os.path.join(save_dir, '%s-e%s.h5' % (dt.datetime.now().strftime('%Y%m%d-%H:%M:%S'), str(epochs)))
        callbacks = [
            EarlyStopping(monitor='val_loss', patience=2),
            ModelCheckpoint(filepath=save_fname, monitor='val_loss', save_best_only=True)
        ]
        self.model.fit(
            x,
            y,
            epochs=epochs,
            batch_size=batch_size,
            callbacks=callbacks,
            validation_split=validation_split
        )
        self.model.save(save_fname)

        print('[Model] Training Completed. Model saved as %s' % save_fname)
        timer.stop()

    def train_generator(self, data_gen, epochs, batch_size, steps_per_epoch, save_dir):
        timer = Timer()
        timer.start()
        print('[Model] Training Started')
        print('[Model] %s epochs, %s batch size, %s batches per epoch' % (epochs, batch_size, steps_per_epoch))
        
        save_fname = os.path.join(save_dir, '%s-e%s.h5' % (dt.datetime.now().strftime('%Y%m%d-%H:%M:%S'), str(epochs)))
        callbacks = [
            ModelCheckpoint(filepath=save_fname, monitor='loss', save_best_only=True),
            EarlyStopping(monitor='val_loss', patience=2)
        ]
        self.model.fit_generator(
            data_gen,
            steps_per_epoch=steps_per_epoch,
            epochs=epochs,
            callbacks=callbacks,
            workers=1
        )
        
        print('[Model] Training Completed. Model saved as %s' % save_fname)
        timer.stop()


def main():
    config = json.load(open('bin\\seq2seq_model\\seq2seq_config.json', 'r', encoding='utf-8'))

    dataloader = DataLoader(config=config, nrows=1000)
    x_open, y_open, x_close, y_close = dataloader.split_open_close_data()
    xy_data_generator = dataloader.generate_x_y_data(x_open, y_open, x_close, y_close)

    seq2seq_model = Seq2Seq_Model(config)
    seq2seq_model.build_model()

    print(x_data.head(5))
    print(y_data.head(5))
    print(y_tag.head(5))

if __name__ == '__main__':
    main()
