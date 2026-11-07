// Independent, dependency-free projection kernel. Matrices are row-major.
// Inputs: xyzxyz segments; output: NDC xyxy. Clips at the camera near plane.
static float points[6*200000];
static float result[4*200000];
static float matrix[16];
__attribute__((export_name("points"))) float *input_ptr(void) { return points; }
__attribute__((export_name("result"))) float *output_ptr(void) { return result; }
__attribute__((export_name("matrix"))) float *matrix_ptr(void) { return matrix; }
__attribute__((export_name("project"))) int project(int count, float near_z) {
  int out = 0;
  if (count > 200000) count = 200000;
  for(int i=0; i<count; i++) {
    float p[2][4];
    for(int j=0;j<2;j++) for(int r=0;r<4;r++) {
      float *v = points+i*6+j*3;
      p[j][r]=matrix[r*4]*v[0]+matrix[r*4+1]*v[1]+matrix[r*4+2]*v[2]+matrix[r*4+3];
    }
    if(p[0][3]<near_z && p[1][3]<near_z) {
      for(int k=0;k<4;k++) result[i*4+k]=4.0f;
      continue;
    }
    if(p[0][3]<near_z || p[1][3]<near_z) {
      int a=p[0][3]<near_z?0:1, b=1-a;
      float t=(near_z-p[a][3])/(p[b][3]-p[a][3]);
      for(int k=0;k<4;k++) p[a][k]+=t*(p[b][k]-p[a][k]);
    }
    result[i*4]=p[0][0]/p[0][3]; result[i*4+1]=p[0][1]/p[0][3];
    result[i*4+2]=p[1][0]/p[1][3]; result[i*4+3]=p[1][1]/p[1][3];
    out++;
  }
  return out;
}
