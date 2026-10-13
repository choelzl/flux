// PTO-INSTRUCTION: {"id":"PTO-TILE-TSUB","mnemonic":"TSUB","surface":"tile","depends_on":["PTO-TILE-TADD"],"summary":"Subtract corresponding elements of two Local Tiles.","classification":["elementwise-tile-tile","arithmetic"]}
readonly func InstructionContractOperandsLegal_TSUB(destination: TileIndex, source_left: TileIndex,
    source_right: TileIndex) => boolean
begin
    return TRUE;
end;

func InstructionContractExecute_TSUB(destination: TileIndex, source_left: TileIndex, source_right: TileIndex)
begin
end;
