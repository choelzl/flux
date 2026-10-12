// PTO-TEST: {"id":"PTO-AVS-TILE-TADD-BROKEN-001","source":"asl/tile/elementwise-tile-tile/arithmetic/TADD.asl","requirements":["PTO-TADD-CONTRACT-001"],"kind":"execution","summary":"a test the fake runner fails"}
func main() => integer
begin
    return 1;   // FAIL
end;
