NUM_TO_TEST=[
    "STORE_ON_MODIFIED"
    ,"STORE_ON_MODIFIED_NO_SYNC"
    ,"STORE_ON_EXCLUSIVE"
    ,"STORE_ON_SHARED"
    ,"STORE_ON_OWNED_MINE"
    ,"STORE_ON_OWNED"
    ,"STORE_ON_INVALID"
    ,"LOAD_FROM_MODIFIED"
    ,"LOAD_FROM_EXCLUSIVE"
    ,"LOAD_FROM_SHARED"
    ,"LOAD_FROM_OWNED"
    ,"LOAD_FROM_INVALID"
    ,"CAS"
    ,"FAI"
    "TAS"
    ,"SWAP"
    ,"CAS_ON_MODIFIED"
    ,"FAI_ON_MODIFIED"
    ,"TAS_ON_MODIFIED"
    ,"SWAP_ON_MODIFIED"
    ,"CAS_ON_SHARED"
    ,"FAI_ON_SHARED"
    ,"TAS_ON_SHARED"
    ,"SWAP_ON_SHARED"
    ,"CAS_CONCURRENT"
    ,"FAI_ON_INVALID"
    ,"LOAD_FROM_L1"
    ,"LOAD_FROM_MEM_SIZE"
    ,"LFENCE"
    ,"SFENCE"
    ,"MFENCE"
    ,"PROFILER"
    ,"PAUSE"
    ,"NOP"
]

# test output has results for each core, this indicates which core's result is appropiate
TARGET_CORE = [1] * 34
TARGET_CORE[6] = 0
TARGET_CORE[11] = 0
TARGET_CORE[25] = 0
TARGET_CORE[26] = 0