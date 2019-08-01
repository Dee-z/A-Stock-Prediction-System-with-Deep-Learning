'''
plot minutes：
	收集了六个月的财经信息，基本来源于新浪财经，新闻数量为80000条左右，经过Bert编码，产生的新闻编码空间是768维。
	对编码空间可视化，试试看。
	对分钟股价数据进行可视化，并发现同一标签数据的规律

'''

import numpy as np 
import pandas as pd 
import sys
import os
from matplotlib import pyplot as plt 

sys.path.append('C:\\Users\\longf.DESKTOP-7QSFE46\\GitHub\\A-Stock-Prediction-System-with-GAN-and-DRL')

def vectorize_news(data, ):
	'''
	vectorize news：
		对news向量化，处理数据,分类并返回dataframe
	'''
	data['channels_list'] = pd.Series().astype(object)
	codes = []
	for i in data.index:
		# 将channel字符串处理成id列表，ID表示新闻的类别
		channel = data.at[i, 'channels']
		s = '\[\]\{\}\''
		for _ in s:
		    channel = channel.replace(_,'')
		l = channel.split(', ')
		for _ in l:
		    _ = _.strip()
		chn = []
		for _ in l:
		    if(_[:2]=='id'):
		        chn.append(int(_[3:]))
		data['channels_list'].at[i] = chn # 将channels转换为类别标签列表

		# 将字符串code转化为列表
		code = data.at[i, 'code']
		code = [float(x) for x in code.lstrip('\[').rstrip('\]').split(', ')]
		codes.append(code)

	vectorized_news = pd.DataFrame(codes, index=data.index)
	vectorized_news['channels_list'] = data['channels_list']

	return vectorized_news

def window_minutes(data):
	'''
	window minutes:
		对分钟线数据进行窗口化，并打标签
	'''
	pass

def plot_news_vector(data, tags=None):
	'''
	plot news vector:
		用散点图绘制不同tags的编码分布情况，从768维中抽取2维
	data:
		向量化之后的data
	tags:
		新闻的标签，列表
	'''
	import random

	x_y = list(range(0,768))
	random.shuffle(x_y) # 从768维中随机选2个不同的维 便于进行可视化
	plot_news = []
	for i in range(10):
		plot_news.append(pd.DataFrame(columns=['x', 'y']))

	for i in data.index:
		channel = data['channels_list'].at[i]
		for j in channel:
			a = {'x':data[x_y[0]].at[i], 'y':data[x_y[1]].at[i]}
			plot_news[j-1] = plot_news[j-1].append(pd.Series(a), ignore_index=True)
	
	plt.figure()
	for i in range(10):
		plt.scatter(plot_news[i]['x'], plot_news[i]['y'], s=10, alpha=0.3, label=tags[i+1])
	plt.legend()
	plt.show()

def plot_window_minutes(data):
	'''
	plot window minutes:
		对窗口化的股价数据进行可视化
	'''
	pass


def main():
	filename = 'dataset\\News_with_code-2019-07-28-to-2019-01-30.csv'
	data_csv = pd.read_csv(filename, nrows=8000).drop(columns=['content']) # 去掉content列 减少内存占用
	
	data = data_csv.set_index(pd.to_datetime(data_csv['datetime'])).drop(columns=[x for x in data_csv.columns if x.startswith('Unnamed: ')])
	data = vectorize_news(data)

	# 新闻类别 一共十个
	names = ['宏观','行业','公司','数据','市场','观点','央行','其他','焦点','A股']
	ids = range(1,11)
	tag = {}
	for i in ids:
		tag[i]=names[i-1]

	# 为了正常显示中文字符
	plt.rcParams['font.sans-serif'] = ['SimHei']# 用来正常显示中文标签
	plt.rcParams['axes.unicode_minus'] = False# 用来正常显示负号

	plot_news_vector(data, tag)

	


if __name__ == '__main__':
	main()