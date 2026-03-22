"""
认证和授权工具
JWT令牌管理和RBAC权限检查
"""

import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from functools import wraps
import jwt
from sqlalchemy.orm import Session

from .models import User, Permission, Role, APIToken


class AuthConfig:
    """认证配置"""
    SECRET_KEY = os.environ.get('AUTH_SECRET_KEY', 'your-secret-key-change-in-production')
    ALGORITHM = 'HS256'
    ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24小时
    REFRESH_TOKEN_EXPIRE_DAYS = 30


class AuthService:
    """认证服务"""
    
    def __init__(self, db_session: Session):
        self.db = db_session
        self.config = AuthConfig
    
    def authenticate_user(self, username: str, password: str) -> Optional[User]:
        """用户认证"""
        user = self.db.query(User).filter(
            (User.username == username) | (User.email == username)
        ).first()
        
        if not user:
            return None
        
        if not user.is_active:
            return None
        
        if not user.verify_password(password):
            return None
        
        # 更新最后登录时间
        user.last_login = datetime.utcnow()
        self.db.commit()
        
        return user
    
    def create_access_token(self, user: User, expires_delta: Optional[timedelta] = None) -> str:
        """创建访问令牌"""
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=self.config.ACCESS_TOKEN_EXPIRE_MINUTES)
        
        payload = {
            'sub': str(user.id),
            'username': user.username,
            'email': user.email,
            'roles': [role.name for role in user.roles],
            'is_superuser': user.is_superuser,
            'exp': expire
        }
        
        token = jwt.encode(payload, self.config.SECRET_KEY, algorithm=self.config.ALGORITHM)
        return token
    
    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """验证令牌并返回payload"""
        try:
            payload = jwt.decode(token, self.config.SECRET_KEY, algorithms=[self.config.ALGORITHM])
            return payload
        except jwt.PyJWTError:
            return None
    
    def get_current_user(self, token: str) -> Optional[User]:
        """根据令牌获取当前用户"""
        payload = self.verify_token(token)
        if not payload:
            return None
        
        user_id = int(payload.get('sub'))
        user = self.db.query(User).filter(User.id == user_id).first()
        
        if not user or not user.is_active:
            return None
        
        return user
    
    def create_api_token(self, user: User, name: str, permissions: List[str] = None, 
                        expires_days: int = 365) -> APIToken:
        """创建API令牌"""
        import secrets
        
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(days=expires_days) if expires_days else None
        
        api_token = APIToken(
            user_id=user.id,
            name=name,
            token=token,
            is_active=True,
            expires_at=expires_at,
            permissions=','.join(permissions) if permissions else ''
        )
        
        self.db.add(api_token)
        self.db.commit()
        
        return api_token
    
    def verify_api_token(self, token: str) -> Optional[User]:
        """验证API令牌"""
        api_token = self.db.query(APIToken).filter(
            APIToken.token == token,
            APIToken.is_active == True
        ).first()
        
        if not api_token:
            return None
        
        if api_token.expires_at and api_token.expires_at < datetime.utcnow():
            return None
        
        # 更新最后使用时间
        api_token.last_used = datetime.utcnow()
        self.db.commit()
        
        return api_token.user
    
    def check_permission(self, user: User, permission_name: str) -> bool:
        """检查用户是否有特定权限"""
        return user.has_permission(permission_name)
    
    def get_user_permissions(self, user: User) -> List[str]:
        """获取用户所有权限"""
        permissions = set()
        
        if user.is_superuser:
            # 超级用户拥有所有权限
            return ['*']
        
        for role in user.roles:
            for permission in role.permissions:
                permissions.add(permission.name)
        
        return list(permissions)


class PermissionChecker:
    """权限检查装饰器"""
    
    def __init__(self, permission_name: str):
        self.permission_name = permission_name
    
    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 这里假设第一个参数是self，并且有current_user和db属性
            # 实际实现需要根据具体的框架调整
            return func(*args, **kwargs)
        return wrapper


def require_permission(permission_name: str):
    """权限检查装饰器（Flask风格）"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 这里需要根据具体的Web框架实现
            # 例如在Flask中可以从g或request中获取current_user
            return func(*args, **kwargs)
        return wrapper
    return decorator


class RBACManager:
    """RBAC权限管理器"""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    def initialize_defaults(self):
        """初始化默认的权限和角色"""
        # 创建权限
        permissions_map = {}
        for perm_name, description, resource_type, action in models.PREDEFINED_PERMISSIONS:
            permission = self.db.query(Permission).filter(Permission.name == perm_name).first()
            if not permission:
                permission = Permission(
                    name=perm_name,
                    description=description,
                    resource_type=resource_type,
                    action=action
                )
                self.db.add(permission)
            permissions_map[perm_name] = permission
        
        self.db.commit()
        
        # 创建角色并关联权限
        for role_name, description, perm_names in models.PREDEFINED_ROLES:
            role = self.db.query(Role).filter(Role.name == role_name).first()
            if not role:
                role = Role(name=role_name, description=description)
                self.db.add(role)
            
            # 清空现有权限
            role.permissions = []
            
            # 添加新权限
            for perm_name in perm_names:
                if perm_name in permissions_map:
                    role.permissions.append(permissions_map[perm_name])
        
        self.db.commit()
        
        # 创建默认管理员用户（如果不存在）
        admin_user = self.db.query(User).filter(User.username == 'admin').first()
        if not admin_user:
            admin_user = User(
                username='admin',
                email='admin@quant-system.com',
                full_name='系统管理员',
                is_superuser=True
            )
            admin_user.set_password('admin123')  # 默认密码，首次登录后必须修改
            self.db.add(admin_user)
            
            # 分配管理员角色
            admin_role = self.db.query(Role).filter(Role.name == 'admin').first()
            if admin_role:
                admin_user.roles.append(admin_role)
        
        self.db.commit()
        return True
    
    def create_team(self, name: str, description: str = '', creator: User = None) -> Team:
        """创建团队"""
        from .models import Team
        
        team = Team(name=name, description=description)
        self.db.add(team)
        
        if creator:
            team.members.append(creator)
        
        self.db.commit()
        return team
    
    def add_user_to_team(self, user: User, team: Team, is_owner: bool = False):
        """添加用户到团队"""
        if user not in team.members:
            team.members.append(user)
            self.db.commit()
        
        # 这里可以添加团队所有者逻辑
        return True
    
    def remove_user_from_team(self, user: User, team: Team):
        """从团队移除用户"""
        if user in team.members:
            team.members.remove(user)
            self.db.commit()
        return True
    
    def get_user_teams(self, user: User) -> List[Team]:
        """获取用户所属的所有团队"""
        return user.teams
    
    def get_team_members(self, team: Team) -> List[User]:
        """获取团队成员"""
        return team.members


# 导入models模块，避免循环导入
from . import models
from .models import Team