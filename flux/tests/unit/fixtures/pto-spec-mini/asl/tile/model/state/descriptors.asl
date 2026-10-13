pure func TileElementBits(data_type: TileDataType) => integer {4,8,16,32,64}
begin
    case data_type of
        when TileDataType_U4X2 => return 4;
        when TileDataType_U8 => return 8;
        when TileDataType_S16, TileDataType_FP16 => return 16;
    end;
end;
