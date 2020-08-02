import numpy as np
import pandas as pd
import socket
import gc
import os
from PyQt5.QtCore import QObject,pyqtSignal
_gain = int.from_bytes(b'\x20\x00',byteorder='big',signed=True) # 8192
_hit = int.from_bytes(b'\x10\x00',byteorder='big',signed=True) # 4096
_value = int.from_bytes(b'\x0f\xff',byteorder='big',signed=True) # 4095
from enum import Enum,unique
'''
    数据分析模块应分为两种模式：
    1.解析存储的二进制文件：
        1.1 针对不同的二进制文件类型-fileType 
        1.1.1 解析普通大小的二进制文件：default
        1.1.2 解析超大的二进制文件：bigFile
        1.1.3 解析分开存储的多个二进制文件：severalFiles
        1.2 针对不同的内存和速度-memoryType
        1.2.1 全部读取到内存(默认模式)：default
            [高内存消耗，对于过大文件可能会倒置memoryError]
        1.2.2 单数据流模式：singalStream
            [开启一个文件读取流，通过流式读取文件来减小内存占用，cpu资源消耗较少，但是会导致IO资源消耗很大]
        1.2.3 多数据流模式：severalStreams
            [开启多个线程，每个线程都有一个数据流来读取数据，使用此模式必须配合多文件模式]
        1.2.4 高并发多数据流模式：highConcurrency
            [开启多个进程，每个进程都有一个数据流来读取数据，使用此模式必须配合多文件模式]
        1.3 垃圾回收-garbageCollect
            True or Flase
    2.参与实时解析接收到的数据
        2.1 存储格式-storageType
        2.1.1 将解析好的文件存储为CSV文件：default
        2.1.2 将文件存储为元数据(二进制格式)文件：binary
        2.1.3 同时存储为元数据和CSV文件：Both
        2.2 存储模式-storageModel
        2.2.1 存储为一个文件（每一种格式一个）：default
        2.2.2 存储为多个文件：severalFiles
        2.3 内存模式-memoryModel
            [是否将数据都存在内存中，选择True将会把所有的数据以DataFrame的格式存储在内存中，
            选择False则会将各个的道的能谱数据和指定的符合能谱数据保存，解析数据将被舍弃]
            True or False
    3.调用C加速
'''

@unique
class DATAMODE(Enum):
    default = 0
    bigData = 1
    smallMemory = 2
    online = 3

class dataAnalyse(QObject):
    receiveSignal = pyqtSignal(int)
    def __init__(self):
        super(dataAnalyse, self).__init__()
        self._badPackage = 0
        self._count = 0
        self._tCount = 0
        self._temTID = 0
        self._readSize = 1024*1024
        self._chnList = []
        self._typeList = []
        self.chnList = []
        self.typeList = ["time/LowGain_gain", "time/LowGain_hit", "time/LowGain",
                         "charge/HighGain_gain", "charge/HighGain_hit", "charge/HighGain"]
        for i in range(0, 36, 1):
            self.chnList.append("chn_" + str(i))
            for j in ["time/LowGain", "charge/HighGain"]:
                self._chnList.append("chn_" + str(i))
                self._chnList.append("chn_" + str(i))
                self._chnList.append("chn_" + str(i))
                self._typeList.append(j + "_gain")
                self._typeList.append(j + "_hit")
                self._typeList.append(j)
        self._chnList.append("SCAinfo")
        self._typeList.append("bounchCrossingID")
        self._chnList.append("SCAinfo")
        self._typeList.append("triggerID")
        self._chnList.append("SCAinfo")
        self._typeList.append("BoardID")
        self._dataFrame = pd.DataFrame(columns=[self._chnList,self._typeList])
        self._threadTag = False

    def __len__(self):
        return self._count

    def badPackage(self):
        return self._badPackage

    def clearBadPackage(self):
        self._badPackage = 0

    def to_dataFrame(self):
        return self._dataFrame

    def setReadSize(self,size: int):
        self._readSize = size

    #load binary data from file/socket
    def load(self,input,**kwargs):
        self._count = 0
        if kwargs.get("filePath", None) is not None:
            file = open(kwargs.get("filePath"),"w")
            file.write(',' + ','.join(self._chnList) + '\n')
            file.write(',' + ','.join(self._typeList) + '\n')
        else:
            file = None
        if isinstance(input,str):
            self.fileSize = os.path.getsize(input)
            self._loadfile(input,source=kwargs.get("source",True),file=file)
        elif isinstance(input,socket.socket):
            self._loadsocket(input,file=file,both=kwargs.get("both",True))

    #auxiliary : search every SSP2E packet and invoking the decode function from file
    def _loadfile(self,path : str,source = True,file: open = None):
        '''
        load data from file ,file can be baniry format file or csv format file.
        :param path: file path
        :param source:bool,if the file is baniry format,take it as true,the default is true.
        :return: None
        '''
        self.lenError = 0
        self.ChipIDError = 0
        self.TriggerIDError = 0
        #load csv
        if not source:
            self._dataFrame = pd.read_csv(path,index_col=0,header=[0,1])
            return None
        #load binary data from target path and store it as readable format.
        f = open(path,'rb')
        buff = f.read(self._readSize)
        pointer = self._readSize
        tails = 0
        #Compared to the previous code, slightly optimized, reduced the number of changes in the list variables
        while buff != 0:
            try:
                header = buff.index(b'\xfa\x5a', tails)
                tails = buff.index(b'\xfe\xee', header)
            except ValueError:
                b = f.read(self._readSize)
                pointer += self._readSize
                print("pointer/fileSize:{0} / {1} \t {3:.2f}\n dataSize:{2};event:{8}\n"
                      "badPackage:{4},lenError:{5},chipIDError:{6},triggerError:{7}".format(
                    pointer,self.fileSize,self._count,pointer/self.fileSize,
                    self._badPackage,self.lenError,self.ChipIDError,self.TriggerIDError,self._temTID+self._tCount*65535
                ))
                if self._temTID+self._tCount*65535 < 0:
                    pass
                if len(b) == 0:
                    break
                buff = buff[tails+3:] + b
                tails = 0
                gc.collect()
                continue
            # check the length of package is correct
            if len(buff[header:tails+4]) == 156:
                self._unpackage(buff[header:tails+4],file=file,both=False)
            else:
                self._badPackage += 1
                self.lenError += 1

    # auxiliary : search every SS2E packet and invoking the decode function from socket
    def _loadsocket(self, s: socket,file: open = None,both = True):
        '''
        会不断的从socket中读取二进制数据，寻找二进制数据中符合条件的包，送入unpackage中进行解包
        当threadTag设置为False时,会将剩下的数据（最大128M）读入，等待解析完成后将退出循环
        :param s: 从socket接收数据
        :param file: 如果给file参数赋值则会同时向文件中传输解析后的数据
        :return: None
        '''
        buff = s.recv(self._readSize)
        tails = 0
        while buff != 0 and self._threadTag:
            try:
                header = buff.index(b'\xfa\x5a', tails)
                tails = buff.index(b'\xfe\xee', header)
            except ValueError:
                b = s.recv(self._readSize)
                if len(b) == 0:
                    break
                buff = buff[tails + 3:] + b
                tails = 0
                gc.collect()
                continue
            # check the length of package is correct
            if len(buff[header:tails + 4]) == 156:
                self._unpackage(buff[header:tails + 4],file=file,both=both)
                self.receiveSignal.emit(1)
            else:
                self._badPackage += 1
        if not self._threadTag:
            b = s.recv(1024*1024*128)
            buff = buff[tails + 3:] + b
            tails = 0
            while buff != 0:
                try:
                    header = buff.index(b'\xfa\x5a', tails)
                    tails = buff.index(b'\xfe\xee', header)
                except ValueError:
                    break
                # check the length of package is correct
                if len(buff[header:tails + 4]) == 156:
                    self._unpackage(buff[header:tails + 4], file=file,both=both)
                    self.receiveSignal.emit(1)
                else:
                    self._badPackage += 1
        if file is not None:
            file.close()
        gc.collect()
        self.receiveSignal.emit(0)

    # auxiliary : decode a SSP2E packet into pd.DataFrame
    # 辅助函数：解析一个SSP2E数据包，将其返回为
    # 注意，在使用网口通讯接收的数据包只会含有一个SCA包
    def _unpackage(self, source,file: open = None,both = True):
        '''
        THE FOEMAT OF DATA PACKAGE:
        HAED    1ROW    2Byte   0-1
        HGain   36ROW   72Byte  2-73
        LGain   36ROW   72Byte  74-145
        BCID    1ROW    2Byte   146-147
        ChipID  1ROW    2Byte   148-149
        Trigger 1ROW    2Byte   150-151
        TAIL    1ROW    2Byte   152-153
        BoardID 1ROW    2Byte   154-155
        ===============================
        :param source:  156Byte
        :param file: if assign a file to this parameter,this function will output the data to the file
        :return: len = 219
        '''
        # check the header and the tail bytes
        if not source[0:2] == b'\xfa\x5a':
            raise ValueError("Packet header does not match.")
        elif not source[152:154] == b'\xfe\xee':
            raise ValueError("Packet tails do not match.")
        charge = [[], [], []]
        # Charge/HighGain information and time/LowGain information
        for j in range(36):
            index = j + 1
            temp = int.from_bytes(source[index * 2:(index + 1) * 2], byteorder='big')
            charge[0].append(bool(temp & _gain))
            charge[1].append(bool(temp & _hit))
            charge[2].append(temp & _value)
            index = j + 37
            temp = int.from_bytes(source[index * 2:(index + 1) * 2], byteorder='big')
            charge[0].append(bool(temp & _gain))
            charge[1].append(bool(temp & _hit))
            charge[2].append(temp & _value)
        data = np.array(np.flip(np.array(charge), axis=1)).transpose((1, 0)).reshape((1,-1))
        # Bunch Crossing ID(I don't known what information Bunch Crossing ID wants to show us)
        index = 73
        temp = int.from_bytes(source[index * 2:(index + 1) * 2], byteorder='big')
        data = np.append(data, temp)
        # ChipID ,to make sure the package is not correct.
        ChipID = int.from_bytes(source[148:150], byteorder='big')
        # triggerID,the same triggerID marks the same event,triggerID will add one every times it trigger,when triggerID adds up to 0xffff,the next triggerID will be 0x0000

        triggerID = int.from_bytes(source[150:152], byteorder='big')
        if self._temTID != 0 and triggerID == 0:
            self._tCount += 1
        self._temTID = triggerID
        data = np.append(data,triggerID+self._tCount * 65,535)
        # Verify that the data is correct
        if ChipID & int.from_bytes(b'\x00\x01', byteorder='big', signed=True):
            boradID = source[-1]
            data = np.append(data,boradID).reshape((-1,219))
            #===================
            if file is not None:
                string =  str(self._count) + "," +','.join(data.astype('str').tolist()[0])
                file.write(string)
                file.write('\n')
                # 如果不设置同时存储，则只会在将数据传入文件流
                if not both:
                    self._count += 1
                    return True
            #===============
            self._dataFrame.at[self._dataFrame.index.size] = data
            self._count += 1
            return True
        else:
            self._badPackage = self._badPackage + 1
            self.ChipIDError += 1
            return False

if __name__ == "__main__":
    myTrack = dataAnalyse()
    myTrack.load("C:\\Users\\jimbook\\Desktop\\20200726_2202_dac300.dat",filePath=".\\data\\mydata.txt")
