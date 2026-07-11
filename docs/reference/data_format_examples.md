# 数据格式示例

钻孔 CSV 首版列：

```csv
borehole_code,x,y,collar_elevation,borehole_depth,layer_index,stratum_code,stratum_name,top_depth,bottom_depth,unit_weight,cohesion,friction_angle,elastic_modulus,poisson_ratio,permeability,water_level
BH01,0,0,0,20,1,1,Fill,0,2,18,8,18,8,0.35,1e-6,1.2
```

VTU 支持 XML UnstructuredGrid 的 ASCII DataArray。字段映射建议识别 `mat_id`、`c`、`phi`、`E` 等常见别名。
