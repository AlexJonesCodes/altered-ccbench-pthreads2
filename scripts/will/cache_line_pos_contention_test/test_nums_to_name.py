NUM_TO_TEST=[    
    "Store On Modified"
    ,"Store On Modified No Sync"
    ,"Store On Exclusive"
    ,"Store On Shared"
    ,"Store On Owned Mine"
    ,"Store On Owned"
    ,"Store On Invalid"
    ,"Load From Modified"
    ,"Load From Exclusive"
    ,"Load From Shared"
    ,"Load From Owned"
    ,"Load From Invalid"
    ,"CAS"
    ,"FAI"
    ,"TAS"
    ,"SWAP"
    ,"CAS On Modified"
    ,"FAI On Modified"
    ,"TAS On Modified"
    ,"SWAP On Modified"
    ,"CAS On Shared"
    ,"FAI On Shared"
    ,"TAS On Shared"
    ,"SWAP On Shared"
    ,"CAS Concurrent"
    ,"FAI On Invalid"
    ,"Load From L1"
    ,"Load From Mem Size"
    ,"LFENCE"
    ,"SFENCE"
    ,"MFENCE"
    ,"Profiler"
    ,"Pause"
    ,"NOP"
    ,"CAS"
]

# test output has results for each core, this indicates which core's result is appropiate
TARGET_CORE = [1] * 34
TARGET_CORE[6] = 0
TARGET_CORE[11] = 0
TARGET_CORE[25] = 0
TARGET_CORE[26] = 0