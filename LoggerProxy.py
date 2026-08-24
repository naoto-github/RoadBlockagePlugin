import logging

class LoggerProxy:
    def __init__(self, LoggerName, FileName):
        self.LoggerName = LoggerName
        logger = logging.getLogger(LoggerName)
        logger.setLevel(20)
        # 直前の実行がkillLogger()を呼べずに終わっている場合に備えて、
        # 同名ロガーに残っているハンドラを一旦すべて外しておく（多重ログ防止）
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        self.sh = logging.StreamHandler()
        logger.addHandler(self.sh)
        self.fh = logging.FileHandler(FileName)
        logger.addHandler(self.fh)
        formatter = logging.Formatter('%(asctime)s:%(lineno)d:%(thread)d:%(levelname)s:%(funcName)s:%(message)s')
        self.fh.setFormatter(formatter)
        self.sh.setFormatter(formatter)

    @property
    def logger(self):
        return logging.Logger.manager.loggerDict[self.LoggerName]

    def killLogger(self):
        self.fh.close()
        self.sh.close()
        del logging.Logger.manager.loggerDict[self.LoggerName]


