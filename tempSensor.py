"""
    Modified MCC 134 probe:
    Purpose:
        Read a single data value for each channel and write to csv file in /home/pi
"""
#%
import csv
import datetime
import os
import time
import numpy as np
from time import sleep
from sys import stdout
from daqhats import mcc134, HatIDs, HatError, TcTypes
from daqhats_utils import select_hat_device, tc_type_to_string
#%%
ds1 = datetime.datetime.fromtimestamp(time.time())
tempName = str(ds1.year)+'-'+str(ds1.month)+'-'+str(ds1.day)+'.csv'

if os.path.isdir('/home/pi/tempProbes/') == False:
    os.mkdir('/home/pi/tempProbes/')
    
if os.path.isfile('/home/pi/tempProbes/'+tempName) == False: #make new file everyday??
    f = open('/home/pi/tempProbes/'+tempName, 'w')
    writer = csv.writer(f)
    heads = ['time','probe1','probe2','probe3','probe4']
    writer.writerow(heads)
    f.close()


# Constants
CURSOR_BACK_2 = '\x1b[2D'
ERASE_TO_END_OF_LINE = '\x1b[0K'


def main():
    """
    This function is executed automatically when the module is run directly.
    """
    tc_type = TcTypes.TYPE_T   # change this to the desired thermocouple type
    delay_between_reads = 1  # Seconds
    channels = (0, 1, 2, 3)

    try:
        # Get an instance of the selected hat device object.
        address = select_hat_device(HatIDs.MCC_134)
        hat = mcc134(address)

        for channel in channels:
            hat.tc_type_write(channel, tc_type)

        try:
            probVa = []
            for channel in channels:
                value = hat.t_in_read(channel)
                if value == mcc134.OPEN_TC_VALUE:
                    probVa.append(np.nan)
                elif value == mcc134.OVERRANGE_TC_VALUE:
                    probVa.append(np.nan)
                elif value == mcc134.COMMON_MODE_TC_VALUE:
                    probVa.append(np.nan)
                else:
                    probVa.append('{:3.2f}'.format(value))

                stdout.flush()

                # Wait the specified interval between reads.
            tstamp = '%02d'%ds1.hour+':'+'%02d'%ds1.minute
            finalLine = [tstamp,probVa[0],probVa[1],probVa[2],probVa[3]]
            f = open('/home/pi/tempProbes/'+tempName, 'a')
            writer = csv.writer(f)
            writer.writerow(finalLine)
            f.close()
            
        except KeyboardInterrupt:
            # Clear the '^C' from the display.
            print(CURSOR_BACK_2, ERASE_TO_END_OF_LINE, '\n')

    except (HatError, ValueError) as error:
        print('\n', error)


if __name__ == '__main__':
    # This will only be run when the module is called directly.
    main()
