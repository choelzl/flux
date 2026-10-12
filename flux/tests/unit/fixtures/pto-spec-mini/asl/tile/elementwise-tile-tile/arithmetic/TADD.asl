// PTO-INSTRUCTION: {"id":"PTO-TILE-TADD","mnemonic":"TADD","surface":"tile","depends_on":[],"summary":"Add corresponding elements of two Local Tiles.","classification":["elementwise-tile-tile","arithmetic"]}
// NDF-BEGIN: PTO-TADD-CONTRACT-001
// ndf: kind=contract level=L1 layer=tile status=accepted
// TADD MUST consume two Local Tile sources and one new Local destination.
// NDF-END: PTO-TADD-CONTRACT-001
// NDF-BEGIN: PTO-TADD-CONTRACT-002
// ndf: kind=contract level=L1 layer=tile status=accepted
// TADD MUST fault before any destination effect when LB0 is missing.
// NDF-END: PTO-TADD-CONTRACT-002
// NDF-BEGIN: PTO-TADD-CONTRACT-003
// ndf: kind=contract level=L1 layer=tile status=accepted
// An implementation SHOULD complete a 1x8 TADD in one cycle and MAY pipeline it.
// NDF-END: PTO-TADD-CONTRACT-003
func InstructionContractExecute_TADD()
begin
end;
