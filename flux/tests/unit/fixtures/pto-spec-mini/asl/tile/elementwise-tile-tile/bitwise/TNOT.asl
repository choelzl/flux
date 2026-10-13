// PTO-INSTRUCTION: {"id":"PTO-TILE-TNOT","mnemonic":"TNOT","surface":"tile","depends_on":[],"summary":"Complement every element of one Local Tile.","classification":["elementwise-tile-tile","bitwise"]}
readonly func InstructionContractOperandsLegal_TNOT(destination: TileIndex, source: TileIndex) => boolean
begin
    return TRUE;
end;

func InstructionContractExecute_TNOT(destination: TileIndex, source: TileIndex)
begin
end;
