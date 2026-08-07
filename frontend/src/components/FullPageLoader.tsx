import { Spin } from 'antd';

export function FullPageLoader() {
  return (
    <div className="full-page-loader" role="status" aria-label="正在加载">
      <div className="loader-mark">MP</div>
      <Spin size="large" />
    </div>
  );
}
