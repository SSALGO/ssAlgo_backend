# -*- coding: utf-8 -*-
"""
Created on Mon Nov  4 08:16:23 2024

@author: ramak
"""

#import MetaTrader5 as mt5
import time
import pandas as pd
import datetime
import numpy as np
from tabulate import tabulate
import pandas_ta as ta
#mt5.initialize()
import math
import warnings

def FIBOVOLABACKTEST(symbol,num,input_days=7):
    point = mt5.symbol_info(symbol).point
    #input_days=7
    df = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 20000)
    if input_days==7:
        mtw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_W1, 0, 2500)
    elif input_days==30:
        mtw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 2500)
    
    df = pd.DataFrame(df)
    df = df.reset_index()
    df['time'] = pd.to_datetime(df['time'], unit='s')
    mtw = pd.DataFrame(mtw)
    mtw = mtw.reset_index()
    mtw['time'] = pd.to_datetime(mtw['time'], unit='s')
    
    if num != 0:
        mtw = mtw.iloc[:-num]
    df['high']=df['high']/point
    df['open']=df['open']/point
    df['close']=df['close']/point
    df['low']=df['low']/point
    df = df[df['time'] < mtw['time'].iloc[-1]]
    dates=mtw['time'].iloc[-1]
    df['DailyRange']=df['high']-df['low']
    df['avg']=(df['open']+df['close'])/2
    df=df.iloc[-10:]
    #st.dataframe(df)
    #st.write(f'dailyavg mean is {df['DailyRange'].mean()}')
    
    #st.write(f'avg mean is {df['avg'].mean()}')
    vola=(df['DailyRange'].mean()/df['avg'].mean())
    vola100=vola*100
    vola365=vola100*math.sqrt(365)
    lastclose=int(mtw['open'].iloc[-1] / point)#df['open'].iloc[-1]
    multiday=(lastclose*vola365/100*(math.sqrt(input_days)))/math.sqrt(365)
    levels = {'0.236': 0.236, '0.382': 0.382, '0.5': 0.5,'0.618': 0.618,'0.786': 0.786,'0.888': 0.888,'1': 1,'1.236': 1.236,'1.272': 1.272,'1.618': 1.618}
    
    lsw = []
    for key, value in levels.items():
        Bw = lastclose + (value * multiday)
        Sw = lastclose - (value * multiday)
        lsw.append([float(Bw * point), float(Sw * point)])
    dfw = pd.DataFrame(lsw, columns=['Buy', 'Sell'], index=levels.keys())
    #print(dfw)
    dfw['Buy']=round(dfw['Buy'],len(str(int(round(1/mt5.symbol_info(symbol).point))))-1)
    dfw['Sell']=round(dfw['Sell'],len(str(int(round(1/mt5.symbol_info(symbol).point))))-1)
    k=dfw.T.to_dict()
    D={'dates':dates,'PriceRange':round(multiday*mt5.symbol_info(symbol).point,len(str(int(round(1/mt5.symbol_info(symbol).point))))-1),'B0.236':k['0.236']['Buy'],'B0.382':k['0.382']['Buy'],'B0.5':k['0.5']['Buy'],'B0.618':k['0.618']['Buy'],'B0.786':k['0.786']['Buy'],'B0.888':k['0.888']['Buy'],'B1':k['1']['Buy'],'B1.236':k['1.236']['Buy'],'B1.272':k['1.272']['Buy'],'B1.618':k['1.618']['Buy']
     ,'S0.236':k['0.236']['Sell'],'S0.382':k['0.382']['Sell'],'S0.5':k['0.5']['Sell'],'S0.618':k['0.618']['Sell'],'S0.786':k['0.786']['Sell'],'S0.888':k['0.888']['Sell'],'S1':k['1']['Sell'],'S1.236':k['1.236']['Sell'],'S1.272':k['1.272']['Sell'],'S1.618':k['1.618']['Sell']}
    return D

def crossover(series1, series2):
    """Returns a boolean series where series1 crosses over series2."""
    return (series1 > series2) & (series1.shift(1) <= series2.shift(1))

def crossunder(series1, series2):
    """Returns a boolean series where series1 crosses under series2."""
    return (series1 < series2) & (series1.shift(1) >= series2.shift(1))


def crossover(series1, series2):
    """Returns 0 where series1 crosses over series2, else returns 1."""
    crossover_bool = (series1 > series2) & (series1.shift(1) <= series2.shift(1))
    return np.where(crossover_bool, 0, 1)

def crossunder(series1, series2):
    """Returns 0 where series1 crosses under series2, else returns 1."""
    crossunder_bool = (series1 < series2) & (series1.shift(1) >= series2.shift(1))
    return np.where(crossunder_bool, 0, 1)
def f_tfUp(_TF_High, _TF_Vol, _TF_VolMA):
    return (
        (_TF_High.shift(3) > _TF_High.shift(4)) &
        (_TF_High.shift(4) > _TF_High.shift(5)) &
        (_TF_High.shift(2) < _TF_High.shift(3)) &
        (_TF_High.shift(1) < _TF_High.shift(2)) &
        (_TF_Vol.shift(3) > _TF_VolMA.shift(3))
    )

def f_tfDown(_TF_Low, _TF_Vol, _TF_VolMA):
    return (
        (_TF_Low.shift(3) < _TF_Low.shift(4)) &
        (_TF_Low.shift(4) < _TF_Low.shift(5)) &
        (_TF_Low.shift(2) > _TF_Low.shift(3)) &
        (_TF_Low.shift(1) > _TF_Low.shift(2)) &
        (_TF_Vol.shift(3) > _TF_VolMA.shift(3))
    )


def compute_fractals_and_zones(df, TF_num=1):
    # Volume Moving Average Calculation
    df[f'Volume_MA_TF{TF_num}'] = df['volume'].rolling(window=6).mean()

    # TF Up and Down conditions

    # Determine Up/Down conditions
    df[f'TF{TF_num}_Up'] = f_tfUp(df['high'], df['volume'], df[f'Volume_MA_TF{TF_num}'])
    df[f'TF{TF_num}_Down'] = f_tfDown(df['low'], df['volume'], df[f'Volume_MA_TF{TF_num}'])

    # Calculate Fractal Up
    df[f'TF{TF_num}_FractalUp'] = 0.0  # Initialize with 0.0
    df.loc[df[f'TF{TF_num}_Up'], f'TF{TF_num}_FractalUp'] = df['high'].shift(3)
    df[f'TF{TF_num}_FractalUp'] = df[f'TF{TF_num}_FractalUp'].replace(0, method='ffill')  # Carry forward previous value
    df[f'TF{TF_num}_FractalUp'] = df[f'TF{TF_num}_FractalUp'].shift(-2)
    df[f'TF{TF_num}_FractalUp'] =df[f'TF{TF_num}_FractalUp'].ffill()
    # Calculate Fractal Down
    df[f'TF{TF_num}_FractalDown'] = 0.0  # Initialize with 0.0
    df.loc[df[f'TF{TF_num}_Down'], f'TF{TF_num}_FractalDown'] = df['low'].shift(3)
    df[f'TF{TF_num}_FractalDown'] = df[f'TF{TF_num}_FractalDown'].replace(0, method='ffill')  # Carry forward previous value
    df[f'TF{TF_num}_FractalDown']=df[f'TF{TF_num}_FractalDown'].shift(-2)
    df[f'TF{TF_num}_FractalDown']=df[f'TF{TF_num}_FractalDown'].ffill()
    # Calculate Fractal Up Zone
    df[f'TF{TF_num}_FractalUpZone'] = 0.0  # Initialize with 0.0
    df.loc[df[f'TF{TF_num}_Up'] & (df['close'].shift(3) >= df['open'].shift(3)), f'TF{TF_num}_FractalUpZone'] = df['close'].shift(3)
    df.loc[df[f'TF{TF_num}_Up'] & (df['close'].shift(3) < df['open'].shift(3)), f'TF{TF_num}_FractalUpZone'] = df['open'].shift(3)
    df[f'TF{TF_num}_FractalUpZone'] = df[f'TF{TF_num}_FractalUpZone'].replace(0, method='ffill')  # Carry forward previous value

    # Calculate Fractal Down Zone
    df[f'TF{TF_num}_FractalDownZone'] = 0.0  # Initialize with 0.0
    df.loc[df[f'TF{TF_num}_Down'] & (df['close'].shift(3) >= df['open'].shift(3)), f'TF{TF_num}_FractalDownZone'] = df['open'].shift(3)
    df.loc[df[f'TF{TF_num}_Down'] & (df['close'].shift(3) < df['open'].shift(3)), f'TF{TF_num}_FractalDownZone'] = df['close'].shift(3)
    df[f'TF{TF_num}_FractalDownZone'] = df[f'TF{TF_num}_FractalDownZone'].replace(0, method='ffill')  # Carry forward previous value

    # Support and Resistance Zones
    df[f'TF{TF_num}_ResZone'] = df[f'TF{TF_num}_FractalUpZone'].shift(-2)
    df[f'TF{TF_num}_ResZone']=df[f'TF{TF_num}_ResZone'].ffill()
    df[f'TF{TF_num}_SupportZone'] = df[f'TF{TF_num}_FractalDownZone'].shift(-2)
    df[f'TF{TF_num}_SupportZone']=df[f'TF{TF_num}_SupportZone'].ffill()

    # Price Crossovers and Crossunders

    # New logic based on crossovers and crossunders
    df[f'PriceEntersTF{TF_num}ResZone'] = crossover(df['close'], df[f'TF{TF_num}_ResZone'])
    df[f'PriceTestResAsSupportTF{TF_num}'] = crossunder(df['close'], df[f'TF{TF_num}_FractalUp'])
    df[f'PriceEntersTF{TF_num}SupZone'] = crossunder(df['close'], df[f'TF{TF_num}_SupportZone'])
    df[f'PriceTestSupportAsResTF{TF_num}'] = crossover(df['close'], df[f'TF{TF_num}_FractalDown'])
    df[f'PriceBreakingTF{TF_num}Resistance'] = crossover(df['close'], df[f'TF{TF_num}_FractalUp'])
    df[f'PriceBreakingTF{TF_num}Support'] = crossunder(df['close'], df[f'TF{TF_num}_FractalDown'])

    # New Resistance and Support Discovery
    TF_Menu = 'S/R Zones'  # Example input, can be dynamically set based on your strategy
    df[f'NewResFoundTF{TF_num}'] = ((TF_Menu == 'S/R Zones') | (TF_Menu == 'S/R')) & (df[f'TF{TF_num}_FractalUp'] != df[f'TF{TF_num}_FractalUp'].shift(1))
    df[f'NewSupFoundTF{TF_num}'] = ((TF_Menu == 'S/R Zones') | (TF_Menu == 'S/R')) & (df[f'TF{TF_num}_FractalDown'] != df[f'TF{TF_num}_FractalDown'].shift(1))

    # Return the modified DataFrame
    return df



from numba import njit
#40secs
@njit
def numba_vwap(temp_vol, temp_wgt, length_arr):
    result = np.full(len(temp_vol), np.nan)  # Pre-allocate result array with NaNs
    
    for i in range(len(temp_vol)):
        if np.isnan(length_arr[i]) or length_arr[i] < 1:
            continue  # Skip if length is NaN or less than 1
        else:
            end = i + 1
            start = max(0, end - int(length_arr[i]))
            uvol = temp_vol[start:end].sum()
            uwgt = temp_wgt[start:end].sum()
            result[i] = uwgt / uvol if uvol != 0 else np.nan

    return result

def get_vwap(df, src, length, vol):
    temp_vol = df[vol].to_numpy()
    temp_wgt = (df[src] * df[vol]).to_numpy()
    length_arr = length.to_numpy()

    # Call the Numba-compiled function
    result = numba_vwap(temp_vol, temp_wgt, length_arr)

    return pd.Series(result, index=df.index)


def get_midas(df, length, is_highest):
    mid = get_vwap(df, 'hlc3', length, 'volume')
    value = get_vwap(df, 'high' if is_highest else 'low', length, 'volume')
    return mid, value

def get_midas_trend(vtop, vbot, close):
    return np.where((np.isnan(vtop) | (close > vtop)) & (close > vbot), 0,
                    np.where((np.isnan(vbot) | (close < vbot)) & (close < vtop), 1, 2))

@njit
def rolling_argmax(arr, window):
    """Calculate the rolling argmax with the given window size."""
    result = np.full(len(arr), np.nan)
    for i in range(window - 1, len(arr)):
        result[i] = window - 1 - np.argmax(arr[i - window + 1:i + 1])
    return result

@njit
def rolling_argmin(arr, window):
    """Calculate the rolling argmin with the given window size."""
    result = np.full(len(arr), np.nan)
    for i in range(window - 1, len(arr)):
        result[i] = window - 1 - np.argmin(arr[i - window + 1:i + 1])
    return result

@njit
def numba_where(cond, val_true, val_false):
    """Numba-friendly version of np.where."""
    result = np.empty_like(cond, dtype=np.float64)
    for i in range(len(cond)):
        result[i] = val_true[i] if cond[i] else val_false[i]
    return result

def nubia_indicator(df, md_show=False):
    df['hlc3'] = (df['high'] + df['low'] + df['close']) / 3

    ma_lengths = [17, 72, 305, 1292]
    high_np = df['high'].to_numpy()
    low_np = df['low'].to_numpy()

    for i, length in enumerate(ma_lengths, 1):
        # Use Numba-accelerated rolling argmax and argmin
        df[f'ma{i}_highbars'] = rolling_argmax(high_np, length)
        df[f'ma{i}_lowbars'] = rolling_argmin(low_np, length)

        # Use the optimized get_midas function (assuming it's Numba-friendly)
        df[f'ma{i}_top_mid'], df[f'ma{i}_top_high'] = get_midas(df, df[f'ma{i}_highbars'], True)
        df[f'ma{i}_bot_mid'], df[f'ma{i}_bot_low'] = get_midas(df, df[f'ma{i}_lowbars'], False)

        # Visibility logic with Numba
        if i > 1:
            df[f'ma{i}_top_visible'] = (df[f'ma{i}_top_high'] > df['low']) & (
                df[f'ma{i-1}_top_high'].isna() | (df[f'ma{i-1}_top_high'] < df[f'ma{i}_top_high']))
            df[f'ma{i}_bot_visible'] = (df[f'ma{i}_bot_low'] < df['high']) & (
                df[f'ma{i-1}_bot_low'].isna() | (df[f'ma{i-1}_bot_low'] > df[f'ma{i}_bot_low']))
        else:
            df[f'ma{i}_top_visible'] = df[f'ma{i}_top_high'] > df['low']
            df[f'ma{i}_bot_visible'] = df[f'ma{i}_bot_low'] < df['high']

        # Handle color logic with Numba-optimized np.where equivalent
        df[f'ma{i}_top_color'] = np.where(df[f'ma{i}_top_visible'], 'red', '#926966' if md_show else np.nan)
        df[f'ma{i}_bot_color'] = np.where(df[f'ma{i}_bot_visible'], 'green', '#527552' if md_show else np.nan)

        # Trend calculation
        df[f'ma{i}_trend'] = get_midas_trend(df[f'ma{i}_top_high'], df[f'ma{i}_bot_low'], df['close'])
        del df[f'ma{i}_top_visible'],df[f'ma{i}_bot_visible'],df[f'ma{i}_top_color'],df[f'ma{i}_bot_color'],df[f'ma{i}_top_mid'],df[f'ma{i}_bot_mid']
        del df[f'ma{i}_highbars'] ,df[f'ma{i}_lowbars']
    df['laggingspan']=df['close'].shift(-26)
    df['dirlagspan']=np.where(df['laggingspan']>df['close'],'up',np.where(df['laggingspan']<df['close'],'dn',None))
    df['dirlagspan']=df['dirlagspan'].ffill()
    return df
def calculate_cvd(df, period):
    df['buying'] = df['volume'] * (df['close'] - df['low']) / (df['high'] - df['low'])
    df['selling'] = df['volume'] * (df['high'] - df['close']) / (df['high'] - df['low'])
    df['delta'] = df['buying'] - df['selling']
    df['cvd'] = df['delta'].rolling(window=period).sum()
    return df


def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def detect_divergence(df, n):
    # Identify high and low pivots
    df['high_pivot'] = (df['high'] > df['high'].shift(n)) & (df['high'] > df['high'].shift(-n))
    df['low_pivot'] = (df['low'] < df['low'].shift(n)) & (df['low'] < df['low'].shift(-n))
    
    # Detect bearish divergence
    df['bearish_div'] = (
        df['high_pivot'] & 
        (df['high'] > df['high'].shift(1)) & 
        (df['cvd'] < df['cvd'].shift(1))
    )
    
    # Detect bullish divergence
    df['bullish_div'] = (
        df['low_pivot'] & 
        (df['low'] < df['low'].shift(1)) & 
        (df['cvd'] > df['cvd'].shift(1))
    )
    
    return df

def durbtrade_bbw(data, length=20, mult=2.0, ema_length=5, offset=0):
    """
    Calculate the Bollinger Bands Width (BBW) and its EMA.
    
    Parameters:
    - data: DataFrame with a 'close' column.
    - length: The length of the Bollinger Bands.
    - mult: The multiplier for the standard deviation.
    - ema_length: The length of the EMA for the BBW.
    - offset: The offset for the EMA.
    
    Returns:
    - DataFrame with the calculated BBW and EMA of BBW.
    """
    data['SMA'] = data['close'].rolling(window=length).mean()
    data['StdDev'] = data['close'].rolling(window=length).std()
    
    data['Upper'] = data['SMA'] + (mult * data['StdDev'])
    data['Lower'] = data['SMA'] - (mult * data['StdDev'])
    
    data['BBW'] = ((data['Upper'] - data['Lower']) / data['SMA'])
    data['BBW_EMA'] = (data['BBW'].ewm(span=ema_length, adjust=False).mean())
    
    # Fill color logic
    data['BBW_Col'] = np.where(data['BBW'] - data['BBW'].shift(1) > 0, '#17ff03', '#ff0000')
    data['Fill_Col'] = np.where(data['BBW'] > data['BBW_EMA'], '#17ff03', '#ff0000')
    data['delta']=np.where(round(data['BBW'] - data['BBW_EMA'],3)>=0.001,0,1)
    # Cross over and cross under logic
    data['CrossOver'] = np.where((data['BBW'] > data['BBW_EMA']) & (data['BBW'].shift(1) <= data['BBW_EMA'].shift(1) ), 0,1)
    data['CrossUnder'] = np.where((data['BBW'] <= data['BBW_EMA']) & (data['BBW'].shift(1) > data['BBW_EMA'].shift(1)), 0, 1)
    data['result']=np.where(data['CrossOver']==0,0,np.where(data['CrossUnder']==0,1,np.nan))
    data['result'].fillna(method='ffill', inplace=True)
    del data['SMA'],data['StdDev'],data['Upper'],data['Lower'],data['BBW'],data['BBW_EMA'],data['BBW_Col'],data['Fill_Col']
    return data
'''
def smma(series, length):
    smma_series = series.ewm(alpha=1/length, adjust=False).mean()
    return smma_series
'''

def smma(series, length):
    smma_series = pd.Series(index=series.index)
    smma_series.iloc[0] = series.iloc[:length].mean()  # Initialize with SMA
    for i in range(1, len(series)):
        smma_series.iloc[i] = (smma_series.iloc[i-1] * (length - 1) + series.iloc[i]) / length
    return smma_series
def is_regular_fractal(high, low, mode):
    if mode == 1:
        return (high.shift(4) < high.shift(2)) & (high.shift(3) <= high.shift(2)) & (high.shift(2) > high.shift(1)) & (high.shift(2) > high)
    elif mode == -1:
        return (low.shift(4) > low.shift(2)) & (low.shift(3) >= low.shift(2)) & (low.shift(2) < low.shift(1)) & (low.shift(2) < low)
    return False

def calculate_alligator(df, jaw_length, teeth_length, lips_length, jaw_offset, teeth_offset, lips_offset):
    asrc = (df['high'] + df['low']) / 2
    jaw_ = smma(asrc, jaw_length)
    teeth_ = smma(asrc, teeth_length)
    lips_ = smma(asrc, lips_length)
    
    df['Jaw'] = jaw_.shift(jaw_offset)
    df['Teeth'] = teeth_.shift(teeth_offset)
    df['Lips'] = lips_.shift(lips_offset)
    
    return df

def calculate_fractals(df):
    df['TopCount'] = 0
    df['BotCount'] = 0
    df['TopFractals'] = np.nan
    df['BotFractals'] = np.nan
    df['TopFractal'] = is_regular_fractal(df['high'], df['low'], 1)
    df['BotFractal'] = is_regular_fractal(df['high'], df['low'], -1)
    
    df['TopCount'] = np.where(df['TopFractal'], 0, df['TopCount'].shift(1) + 1)
    df['BotCount'] = np.where(df['BotFractal'], 0, df['BotCount'].shift(1) + 1)
    
    df['TopFractals'] = np.where(df['TopFractal'], df['high'].shift(2), df['TopFractals'].shift(1))
    df['BotFractals'] = np.where(df['BotFractal'], df['low'].shift(2), df['BotFractals'].shift(1))
    df['TopFractals'].fillna(method='ffill', inplace=True)
    df['BotFractals'].fillna(method='ffill', inplace=True)
    df['fractaldir']=np.where((df['close'].shift(1) < df['TopFractals']) & (df['close'] > df['TopFractals']),1,np.where((df['close'].shift(1) > df['BotFractals']) & (df['close'] < df['BotFractals']),-1,0))
    
    
    return df

def mainalligator(df):
    jaw_length = 13
    teeth_length = 8
    lips_length = 5
    jaw_offset = 8
    teeth_offset = 5
    lips_offset = 3
    
    df = calculate_alligator(df, jaw_length, teeth_length, lips_length, jaw_offset, teeth_offset, lips_offset)
    df = calculate_fractals(df)
    
    # Further calculations and plotting logic here
    
    return df

def alpha_trend1(df):
    import pandas_ta as ta
    open_price, close, high, low, volume = df['open'], df['close'], df['high'], df['low'], df['volume'].apply(int)
    high = high.astype(np.float64)  
    low = low.astype(np.float64)  
    close = close.astype(np.float64)  
    volume = volume.astype(np.float64)  
    ap, coeff = 14, 1
    noVolumeData = True
    tr = ta.true_range(high, low, close)
    atr=ta.sma(tr, ap)
    upt, down_t, hlc3, k1, k2, alpha_trend = [], [], [], [], [], [0.0]
    src = close
    rsi= ta.rsi(src, 14)
    mfi= ta.mfi(high, low, close, volume, 14)

    for i in range(len(close)):
        hlc3.append((high[i] + low[i] + close[i]) / 3)

    for i in range(len(low)):
        upt.append(low[i] - (atr[i] * coeff) if not pd.isna(atr[i]) else 0)

    for i in range(len(high)):
        down_t.append(high[i] + (atr[i] * coeff) if not pd.isna(atr[i]) else 0)

    for i in range(1, len(close)):
        if (not noVolumeData and mfi[i] >= 50) or (noVolumeData and rsi[i] >= 50):
            alpha_trend.append(upt[i] if upt[i] >= alpha_trend[i - 1] else alpha_trend[i - 1])
        else:
            alpha_trend.append(down_t[i] if down_t[i] <= alpha_trend[i - 1] else alpha_trend[i - 1])

    for i in range(len(alpha_trend)):
        k2.append(0 if i < 2 else alpha_trend[i - 2])
        k1.append(alpha_trend[i])

    at = pd.DataFrame(data=k1, columns=['k1'])
    at['out']=np.where((at['k1']>at['k1'].shift(2)),1,np.where((at['k1']<at['k1'].shift(2)  ) ,-1,0))
    at['k2'] = k2

    def determine_result(row):
        if row['k1'] <= row['k2'] and row['k1_shifted'] > row['k2_shifted']:
            return 0  # Buy
        elif row['k1'] >= row['k2'] and row['k1_shifted'] < row['k2_shifted']:
            return 1  # Sell
        else:
            return None  # No action

    # Shift the columns to compare current and previous values
    at['k1_shifted'] = at['k1'].shift(1)
    at['k2_shifted'] = at['k2'].shift(1)

    # Apply the custom function to create the 'result' column
    at['result'] = at.apply(determine_result, axis=1)

    # Drop the shifted columns if you don't need them in the final result
    at = at.drop(['k1_shifted', 'k2_shifted'], axis=1)
    print(at)
    df['out']=at['out']
    df['result']=at['result']
    df['k1']=at['k1']
    df['k2']=at['k2']
    return df

def alpha_trend(df):  
    import pandas as pd  
    import numpy as np  
    import pandas_ta as ta  

    # Ensure consistent data types with explicit conversion  
    open_price = df['open'].astype(np.float64)  
    close = df['close'].astype(np.float64)  
    high = df['high'].astype(np.float64)  
    low = df['low'].astype(np.float64)  
    print('ggggggggggggggggggggggggggggggg')
    # Handle volume conversion more robustly  
    try:  
        # First try to convert directly to float64  
        volume = df['volume'].astype(np.float64)  
    except Exception:  
        # If direct conversion fails, try to clean the data first  
        volume = pd.to_numeric(df['volume'], errors='coerce').fillna(0).astype(np.float64)  

    ap, coeff = 14, 1  
    noVolumeData = volume.sum() == 0  # Check if volume is essentially empty  

    # Compute True Range and Average True Range  
    tr = ta.true_range(high, low, close)  
    atr = ta.sma(tr, ap)  

    # Compute HLC3  
    hlc3 = (high + low + close) / 3  

    # Compute upper and lower trend lines  
    upt = low - (atr * coeff)  
    down_t = high + (atr * coeff)  

    # Compute RSI and MFI  
    rsi = ta.rsi(close, 14)  
    
    # Only compute MFI if volume data is meaningful  
    if not noVolumeData:  
        mfi = ta.mfi(pd.Series(high), pd.Series(low), pd.Series(close), pd.Series(volume), 14)  

    else:  
        # Create a placeholder MFI that mirrors RSI if no volume data  
        mfi = rsi.copy()  

    # Alpha Trend Calculation  
    alpha_trend = np.zeros_like(close)  
    alpha_trend[0] = close[0]  

    for i in range(1, len(close)):  
        # Condition for trend direction  
        if (noVolumeData and rsi[i] >= 50) or (not noVolumeData and mfi[i] >= 50):  
            alpha_trend[i] = max(upt[i], alpha_trend[i-1])  
        else:  
            alpha_trend[i] = min(down_t[i], alpha_trend[i-1])  

    # Create DataFrame for analysis  
    at = pd.DataFrame({  
        'k1': alpha_trend,  
        'k2': np.roll(alpha_trend, 2)  # Shifted version  
    })  

    # Trend Change Detection  
    at['out'] = np.where(at['k1'] > at['k1'].shift(2), 1,   
                         np.where(at['k1'] < at['k1'].shift(2), -1, np.nan))  

    # Simplified result determination  
    def determine_result(row):  
        k1, k1_prev = row['k1'], row['k1_shifted']  
        k2, k2_prev = row['k2'], row['k2_shifted']  
        
        if k1 <= k2 and k1_prev > k2_prev:  
            return 0  # Buy  
        elif k1 >= k2 and k1_prev < k2_prev:  
            return 1  # Sell  
        return None  

    # Shift columns for comparison  
    at['k1_shifted'] = at['k1'].shift(1)  
    at['k2_shifted'] = at['k2'].shift(1)  

    # Apply result determination  
    at['result'] = at.apply(determine_result, axis=1)  

    # Add columns to original DataFrame  
    df['out'] = at['out']
    df['out']=df['out'].ffill()
    df['result'] = at['result']  
    df['k1'] = at['k1']  
    df['k2'] = at['k2']  

    return df


def alphatrend_cal(df):  
    Open = df['open']
    Close = df['close']
    High = df['high']
    Low = df['low']
    Volume = df['volume']
    ap = 14
    tr = ta.true_range(High, Low, Close)
    atr = ta.sma(tr, ap)
    noVolumeData = False
    coeff = 1
    upt = []
    downT = []
    AlphaTrend = [0.0]
    src = Close
    rsi = ta.rsi(src, 14)
    hlc3 = []
    k1 = []
    k2 = []
    mfi = ta.mfi(High, Low, Close, Volume, 14)
    for i in range(len(Close)):
        hlc3.append((High[i] + Low[i] + Close[i]) / 3)

    for i in range(len(Low)):
        if pd.isna(atr[i]):
            upt.append(0)
        else:
            upt.append(Low[i] - (atr[i] * coeff))
    for i in range(len(High)):
        if pd.isna(atr[i]):
            downT.append(0)
        else:
            downT.append(High[i] + (atr[i] * coeff))
    for i in range(1, len(Close)):
        if noVolumeData is True and rsi[i] >= 50:
            if upt[i] < AlphaTrend[i - 1]:
                AlphaTrend.append(AlphaTrend[i - 1])
            else:
                AlphaTrend.append(upt[i])

        elif noVolumeData is False and mfi[i] >= 50:
            if upt[i] < AlphaTrend[i - 1]:
                AlphaTrend.append(AlphaTrend[i - 1])
            else:
                AlphaTrend.append(upt[i])
        else:
            if downT[i] > AlphaTrend[i - 1]:
                AlphaTrend.append(AlphaTrend[i - 1])
            else:
                AlphaTrend.append(downT[i])

    for i in range(len(AlphaTrend)):
        if i < 2:
            k2.append(0)
            k1.append(AlphaTrend[i])
        else:
            k2.append(AlphaTrend[i - 2])
            k1.append(AlphaTrend[i])
            
    df['k1'] = k1
    df['k2'] = k2
    df['out'] = np.where(df['k1'] > df['k1'].shift(2), 1,np.where(df['k1'] < df['k1'].shift(2), -1, np.nan))  
    df['out']=df['out'].ffill()
    return df
