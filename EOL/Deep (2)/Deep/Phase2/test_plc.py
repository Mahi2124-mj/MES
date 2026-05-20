import pymcprotocol

PLC_IP = '192.168.10.150'
PLC_PORT = 5002

plc = pymcprotocol.Type4E()
plc.connect(PLC_IP, PLC_PORT)

# Read status word
status = plc.batchread_wordunits(headdevice='D6005', readsize=1)
print(f"Status word: {status}")

# Read OK bit
ok = plc.batchread_bitunits(headdevice='L108', readsize=1)
print(f"OK bit: {ok}")

# Read NG bit
ng = plc.batchread_bitunits(headdevice='L109', readsize=1)
print(f"NG bit: {ng}")

# Read model word
model = plc.batchread_wordunits(headdevice='D6048', readsize=1)
print(f"Model word: {model}")

plc.close()