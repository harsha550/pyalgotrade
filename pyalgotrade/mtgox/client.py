# PyAlgoTrade
#
# Copyright 2011-2013 Gabriel Martin Becedillas Ruiz
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
.. moduleauthor:: Gabriel Martin Becedillas Ruiz <gabriel.becedillas@gmail.com>
"""


from pyalgotrade import observer
import pyalgotrade.logger
import wsclient
import httpclient

import threading
import Queue
import time

logger = pyalgotrade.logger.getLogger("mtgox")


# This class is responsible for handling events running in the WebSocketClient thread and putting
# them in a queue.
class WSClient(wsclient.WebSocketClient):

    # Events
    ON_TICKER = 1
    ON_WALLET = 2
    ON_TRADE = 3
    ON_USER_ORDER = 4
    ON_RESULT = 5
    ON_REMARK = 6
    ON_CONNECTED = 7
    ON_DISCONNECTED = 8

    # currency is the account's currency.
    def __init__(self, currency, apiKey, apiSecret, ignoreMultiCurrency):
        wsclient.WebSocketClient.__init__(self, currency, apiKey, apiSecret, ignoreMultiCurrency)
        self.__queue = Queue.Queue()

    def getQueue(self):
        return self.__queue

    def onOpened(self):
        self.__queue.put((WSClient.ON_CONNECTED, None))

    def onClosed(self, code, reason):
        logger.info("Closed. Code: %s. Reason: %s." % (code, reason))

    def onDisconnectionDetected(self):
        self.close_connection()
        self.__queue.put((WSClient.ON_DISCONNECTED, None))

    def onSubscribe(self, data):
        logger.info("Subscribe: %s." % (data))

    def onUnsubscribe(self, data):
        logger.info("Unsubscribe: %s." % (data))

    def onRemark(self, data):
        if "id" in data:
            self.__queue.put((WSClient.ON_REMARK, (data["id"], data)))
        else:
            logger.info("Remark: %s" % (data))

    def onUnknownOperation(self, operation, data):
        logger.warning("Unknown operation %s: %s" % (operation, data))

    def onResult(self, data):
        self.__queue.put((WSClient.ON_RESULT, (data["id"], data["result"])))

    def onTicker(self, ticker):
        self.__queue.put((WSClient.ON_TICKER, ticker))

    def onTrade(self, trade, publicChannel):
        # As described in https://en.bitcoin.it/wiki/MtGox/API/Streaming#trade_2
        # Trades that happen on behalf of a user whose private channel you're subscribed to issue trade
        # messages with the same format as the public trades channel does.
        # 
        # We skip those trades from the private channel to avoid duplicate processing of the same trade.
        if publicChannel:
            self.__queue.put((WSClient.ON_TRADE, trade))


    def onWallet(self, wallet):
        self.__queue.put((WSClient.ON_WALLET, wallet))

    def onUserOrder(self, userOrder):
        self.__queue.put((WSClient.ON_USER_ORDER, userOrder))


class Client(observer.Subject):
    """This class is responsible for all trading interaction with MtGox.

    :param currency: The account's currency. Valid values are: USD, AUD, CAD, CHF, CNY, DKK, EUR, GBP, HKD, JPY, NZD, PLN, RUB, SEK, SGD, THB, NOK or CZK.
    :type currency: string.
    :param apiKey: Your API key. Set this to None for paper trading.
    :type apiKey: string.
    :param apiSecret: Your API secret. Set this to None for paper trading.
    :type apiSecret: string.
    :param ignoreMultiCurrency: Ignore multi currency trades.
    :type ignoreMultiCurrency: boolean.

    .. note::
        For apiKey and apiSecret check the **Application and API access** section in mtgox.com.
    """
    QUEUE_TIMEOUT = 0.01

    # currency is the account's currency.
    def __init__(self, currency, apiKey, apiSecret, ignoreMultiCurrency=False):
        if currency not in ["USD", "AUD", "CAD", "CHF", "CNY", "DKK", "EUR", "GBP", "HKD", "JPY", "NZD", "PLN", "RUB", "SEK", "SGD", "THB", "NOK", "CZK"]:
            raise Exception("Invalid currency")

        self.__currency = currency
        self.__apiKey = apiKey
        self.__apiSecret = apiSecret
        self.__ignoreMultiCurrency = ignoreMultiCurrency

        self.__thread = None
        self.__initializationOk = None
        self.__stopped = False
        self.__tickerEvent = observer.Event()
        self.__tradeEvent = observer.Event()
        self.__userOrderEvent = observer.Event()
        self.__walletEvent = observer.Event()
        self.__wsClient = None
        self.__enableReconnection = True
        self.__resultCB = {}
        self.__remarkCB = {}

        # Build papertrading/livetrading objects.
        if apiKey is None or apiSecret is None:
            self.__paperTrading = True
            self.__httpClient = None
        else:
            self.__paperTrading = False
            self.__httpClient = httpclient.HTTPClient(apiKey, apiSecret, currency)

    def __threadMain(self):
        self.__wsClient.startClient()
        # logger.info("Thread finished.")

    def __registerCallbacks(self, requestId, resultCB, remarkCB):
        assert(requestId not in self.__resultCB)
        assert(requestId not in self.__remarkCB)
        self.__resultCB[requestId] = resultCB
        self.__remarkCB[requestId] = remarkCB

    def __unregisterCallbacks(self, requestId):
        assert(requestId in self.__resultCB)
        assert(requestId in self.__remarkCB)
        del self.__remarkCB[requestId]
        del self.__resultCB[requestId]

    def __callbacksRegistered(self, requestId):
        return requestId in self.__resultCB and requestId in self.__remarkCB

    def __initializeClient(self):
        logger.info("Initializing MtGox client.")

        # We use the streaming client only to get updates and not to send requests (using authCall)
        # because when placing orders sometimes we were receving the order update before the result
        # with the order GUID.
        self.__initializationOk = None
        self.__wsClient = WSClient(self.__currency, self.__apiKey, self.__apiSecret, self.__ignoreMultiCurrency)
        self.__wsClient.connect()

        # Start the thread that will run the client.
        self.__thread = threading.Thread(target=self.__threadMain)
        self.__thread.start()

        # Wait for initialization to complete.
        while self.__initializationOk is None and self.__thread.is_alive():
            self.dispatchImpl([WSClient.ON_CONNECTED])
        if self.__initializationOk:
            logger.info("Initialization ok.")
        else:
            logger.error("Initialization failed.")
        return self.__initializationOk

    def waitResponse(self, requestId, resultCB, remarkCB, timeout):
        self.__registerCallbacks(requestId, resultCB, remarkCB)

        done = False
        start = time.time()
        # dispatchImpl may raise if the callbacks raise, but I still want to cleanup the callback mappings.
        try:
            while not done and time.time() - start < timeout:
                self.dispatchImpl([WSClient.ON_RESULT, WSClient.ON_REMARK])
                if not self.__callbacksRegistered(requestId):
                    done = True
        finally:
            # If we timed out then we need to remove the callbacks.
            if not done:
                self.__unregisterCallbacks()
        return done

    def getHTTPClient(self):
        return self.__httpClient

    def getCurrency(self):
        return self.__currency

    def setEnableReconnection(self, enable):
        self.__enableReconnection = enable

    def requestPrivateIdKey(self):
        out = {"result":None}
        def onResult(data):
            out["result"] = data

        def onRemark(data):
            logger.error("Remark requesting private id key: %s" % (data))

        logger.info("Requesting private id key.")
        requestId = self.__wsClient.requestPrivateIdKey()
        self.waitResponse(requestId, onResult, onRemark, 30)

        ret = out["result"]
        if ret in (None, ""):
            raise Exception("Failed to get private id key")
        return ret

    def __onConnected(self):
        logger.info("Connection opened.")

        try:
            # Remove public depth notifications channel to reduce noise.
            logger.info("Unsubscribing from depth notifications channel.")
            self.__wsClient.unsubscribeChannel(wsclient.PublicChannels.getDepthChannel(self.__currency))

            if not self.__paperTrading:
                # Request the Private Id Key and subsribe to private channel.
                privateIdKey = self.requestPrivateIdKey()
                logger.info("Subscribing to private channel.")
                self.__wsClient.subscribePrivateChannel(privateIdKey)
            self.__initializationOk = True
        except Exception, e:
            self.__initializationOk = False
            logger.error(str(e))

    def __onDisconnected(self):
        logger.error("Disconnection detected")
        if self.__enableReconnection:
            initialized = False
            while not self.__stopped and not initialized:
                logger.info("Reconnecting")
                initialized = self.__initializeClient()
                if not initialized:
                    time.sleep(5)
        else:
            self.__stopped = True

    def __onRemark(self, requestId, data):
        try:
            cb = self.__remarkCB[requestId]
            self.__unregisterCallbacks(requestId)
            if cb:
                cb(data)
        except KeyError:
            logger.warning("Remark for request %s: %s" % (requestId, data))

    def __onResult(self, requestId, data):
        try:
            cb = self.__resultCB[requestId]
            self.__unregisterCallbacks(requestId)
            if cb:
                cb(data)
        except KeyError:
            logger.warning("Result for request %s: %s" % (requestId, data))

    def getTickerEvent(self):
        return self.__tickerEvent

    def getTradeEvent(self):
        return self.__tradeEvent

    def getUserOrderEvent(self):
        return self.__userOrderEvent

    def getWalletEvent(self):
        return self.__walletEvent
 
    def start(self):
        if self.__thread is not None:
            raise Exception("Already running")
        elif not self.__initializeClient():
            self.__stopped = True
            raise Exception("Initialization failed")

    def stop(self):
        try:
            self.__stopped = True
            if self.__thread is not None and self.__thread.is_alive():
                logger.info("Shutting down MtGox client.")
                self.__wsClient.stopClient()
        except Exception, e:
            logger.error("Error shutting down MtGox client: %s" % (str(e)))

    def join(self):
        if self.__thread is not None:
            self.__thread.join()

    def eof(self):
        return self.__stopped

    def dispatchImpl(self, eventFilter):
        ret = False
        try:
            eventType, eventData = self.__wsClient.getQueue().get(True, Client.QUEUE_TIMEOUT)
            if eventFilter is not None and eventType not in eventFilter:
                return False

            ret = True
            if eventType == WSClient.ON_TICKER:
                self.__tickerEvent.emit(eventData)
            elif eventType == WSClient.ON_WALLET:
                self.__walletEvent.emit(eventData)
            elif eventType == WSClient.ON_TRADE:
                self.__tradeEvent.emit(eventData)
            elif eventType == WSClient.ON_USER_ORDER:
                self.__userOrderEvent.emit(eventData)
            elif eventType == WSClient.ON_RESULT:
                ret = False
                requestId, data = eventData
                self.__onResult(requestId, data)
            elif eventType == WSClient.ON_REMARK:
                ret = False
                requestId, data = eventData
                self.__onRemark(requestId, data)
            elif eventType == WSClient.ON_CONNECTED:
                self.__onConnected()
            elif eventType == WSClient.ON_DISCONNECTED:
                self.__onDisconnected()
            else:
                ret = False
                logger.error("Invalid event received to dispatch: %s - %s" % (eventType, eventData))
        except Queue.Empty:
            pass
        return ret

    def dispatch(self):
        return self.dispatchImpl(None)

    def peekDateTime(self):
        # Return None since this is a realtime subject.
        return None

    def getDispatchPriority(self):
        # The number is irrelevant since the broker and barfeed will dispatch while processing events.
        return 100
